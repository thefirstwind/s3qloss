'''
backends/oss.py - this file is part of OSSFS (http://ossfs-oos.googlecode.com)

Copyright (C) Xing Xiaoning  <thefirstwind@gmail.com>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import
from .common import AbstractBackend, NoSuchObject, retry, AuthorizationError, http_connection,\
    AuthenticationError, DanglingStorageURLError
from ..common import BUFSIZE,QuietError
import logging
import base64
import sha
import re
import hashlib
import hmac
import sys
import time
import tempfile
import xml.etree.cElementTree as ElementTree
import urllib
from urlparse import urlsplit
import httplib
from httplib import _MAXLINE, CONTINUE, LineTooLong
from xml.etree.ElementTree import ParseError
from s3ql.backends.common import is_temp_network_error


# Pylint goes berserk with false positives
#pylint: disable=E1002,E1101,W0201

C_DAY_NAMES = [ 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun' ]
C_MONTH_NAMES = [ 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec' ]

XML_CONTENT_RE = re.compile(r'^(?:application|text)/xml(?:;|$)', re.IGNORECASE)

log = logging.getLogger("backends.oss")

#class Backend(s3c.Backend):
class Backend(AbstractBackend):
    """A backend to stored data in Google Storage
    
    This class uses standard HTTP connections to connect to GS.
    
    The backend guarantees immediate get consistency and eventual list
    consistency.
    """

    use_expect_100c = True
    
    def __init__(self, storage_url, oss_key, oss_secret, use_ssl):
#        super(Backend, self).__init__(storage_url, oss_key, oss_secret, use_ssl)
        super(Backend, self).__init__()
        
        (host, port, bucket_name, prefix) = self._parse_storage_url(storage_url, use_ssl)

        self.bucket_name = bucket_name
        self.prefix = prefix
        self.hostname = host
        self.port = port
        self.use_ssl = use_ssl
        self.conn = self._get_conn()

        self.password = oss_secret
        self.login = oss_key
#        self.namespace = 'http://s3.amazonaws.com/doc/2006-03-01/'
#        self.namespace = 'http://doc.oss.aliyuncs.com'
        self.namespace = ''


    @staticmethod
    def _parse_storage_url(storage_url, use_ssl):
        hit = re.match(r'^oss://([^/]+)(?:/(.*))?$', storage_url)
        # print("storage_url:%s" % storage_url)
#        hit =  re.match(r'^[a-zA-Z0-9]+://'     # Backend
#                        r'([^/:]+)'             # Hostname
#                        r'(?::([0-9]+))?'       # Port 
#                        r'/([^/]+)'             # Bucketname
#                        r'(?:/(.*))?$',         # Prefix
#                        storage_url)
        if not hit:
            raise QuietError('Invalid storage URL')

        bucket_name = hit.group(1)
        #hostname = '%s.oss-internal.aliyuncs.com' % bucket_name
        hostname = '%s.oss.aliyuncs.com' % bucket_name

        prefix = hit.group(2) or ''
        port = 443 if use_ssl else 80
        return (hostname, port, bucket_name, prefix)

#        hostname = hit.group(1)
#        if hit.group(2):
#          port = int(hit.group(2))
#        elif use_ssl:
#          port = 443
#        else:
#          port = 80
#        bucketname = hit.group(3)
#        prefix = hit.group(4) or ''

#        return (hostname, port, bucketname, prefix)

#    def __str__(self):
#        return 'oss://%s/%s' % (self.bucket_name, self.prefix)

    def _get_conn(self):
        '''Return connection to server'''
        return http_connection(self.hostname, self.port, self.use_ssl)

    def is_temp_failure(self, exc): #IGNORE:W0613
        '''Return true if exc indicates a temporary error

        Return true if the given exception indicates a temporary problem. Most instance methods
        automatically retry the request in this case, so the caller does not need to worry about
        temporary failures.

        However, in same cases (e.g. when reading or writing an object), the request cannot
        automatically be retried. In these case this method can be used to check for temporary
        problems and so that the request can be manually restarted if applicable.
        '''
        if isinstance(exc, (InternalError, BadDigestError, IncompleteBodyError, 
                            RequestTimeoutError, OperationAbortedError, SlowDownError, 
                            RequestTimeTooSkewedError)):
            return True
        elif is_temp_network_error(exc):
            return True

        elif isinstance(exc, HTTPError) and exc.status >= 500 and exc.status <= 599:
            return True

        return False

    @retry
    def delete(self, key, force=False):
        '''Delete the specified object'''

        log.debug('delete(%s)', key)
        try:
            print("method:delete._do_request")
            resp = self._do_request('DELETE', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except NoSuchKeyError:
            if force:
                pass
            else:
                raise NoSuchObject(key)

    def list(self, prefix=''):
        '''List keys in backend

        Returns an iterator over all keys in the backend. This method
        handles temporary errors.
        '''

        log.debug('list(%s): start', prefix)
        print('[method]list(%s): start -----------------------------------', prefix)

        marker = ''
        waited = 0
        interval = 1 / 50
        iterator = self._list(prefix, marker)
        print("[process] list.iterator : %s " % iterator)
        while True:
            try:
                marker = iterator.next()
                print("[method] list >[try] marker")
                waited = 0
            except StopIteration:
                print("[method] list >[exception] StopIteration")
                break
            except Exception as exc:
                print("[method] list >[exception] Exceiption")
                if not self.is_temp_failure(exc):
                    raise
                if waited > 60 * 60:
                    log.error('list(): Timeout exceeded, re-raising %s exception',
                              type(exc).__name__)
                    raise

                log.info('Encountered %s exception (%s), retrying call to s3c.Backend.list()',
                          type(exc).__name__, exc)

                if hasattr(exc, 'retry_after') and exc.retry_after:
                    interval = exc.retry_after

                time.sleep(interval)
                waited += interval
                interval = min(5*60, 2*interval)
                iterator = self._list(prefix, marker)

            else:
                print("[method] list > [else]")
                yield marker

    def _list(self, prefix='', start=''):
        '''List keys in backend, starting with *start*

        Returns an iterator over all keys in the backend. This method
        does not retry on errors.
        '''

        print('[method]_list(%s): start -----------------------------------', prefix)
        keys_remaining = True
        marker = start
        prefix = self.prefix + prefix

        while keys_remaining:

            log.debug('list(%s): requesting with marker=%s', prefix, marker)
            # print("[process] _list(%s): requesting with marker = %s " % (self.prefix,prefix))

            keys_remaining = None
#            print("method: _list._do_request")
#            print("method: _list.prefix(%s)" % prefix)
            resp = self._do_request('GET', '/', query_string={ 'prefix': prefix,
                                                               'marker': marker,
                                                               'max-keys': 1000 })

            if not XML_CONTENT_RE.match(resp.getheader('Content-Type')):
                raise RuntimeError('unexpected content type: %s' % resp.getheader('Content-Type'))


            print("\t[try]_list.try")
#            print("\t[resp] %s" % resp.read())

            itree = iter(ElementTree.iterparse(resp, events=("start", "end")))

            (event, root) = itree.next()
#            print("\t[root] %s" % root)

            '''
            namespace = re.sub(r'^\{(.+)\}.+$', r'\1', root.tag)
            print("\t[process]self.namespace: %s " % self.namespace)
            print("\t[process]namespace %s" % namespace)
            '''
            '''
            log.debug("root.tag %",root.tag)
            log.debug("Prefix: %s" % root.findtext('Prefix'))
            log.debug("Marker: %s" % root.findtext('Marker'))
            log.debug("MaxKeys: %s" % root.findtext('MaxKeys'))
            log.debug("Delimiter: %s" % root.findtext('Delimiter'))
            log.debug("IsTruncated: %s" % root.findtext('IsTruncated'))
            '''

#                if namespace != self.namespace:
#                     raise RuntimeError('Unsupported namespace: %s' % namespace)
            try:

                if root.find('IsTruncated').text == 'true':
                    keys_remaining = true

                for contents in root.findall('Contents'):
    #                print("[>>>>Contents<<<<] %s:%s" % (child.tag, child.text))
                    print("[>>>>Contents<<<<] %s" % contents)
                    for childInContents in contents:
                        print("[childInContents] %s:%s " % (childInContents.tag,childInContents.text))
                        if childInContents.tag == 'Key':
                              marker = childInContents.text
                              yield marker[len(self.prefix):]
                              root.clear()
    #            for (event, el) in itree:
    #                print("\t\t[process] %s:%s" % (el.tag,el.text))

                '''
                for (event, el) in itree:


                    if event != 'end':
                        continue

#                    print("\t\t[process] -----------")
#                    print("\t\t[process]root.tag %s" % el.findtext('ListBucketResult'))
#                    print("\t\t[process] %s:%s" % (el.tag,el.text))
#                    print("\t\t[process] keys_remaining(%s)" % keys_remaining)

#                    if el.tag == 'ListBucketResult':
#                        log.debug("root.tag %s", el.findtext('ListBucketResult'))

#                    print("\t[process]self.namespace:(%s)" % self.namespace)
                    if el.tag == '{%s}IsTruncated' % self.namespace:
                        print("\t[process.IsTruncated]IsTruncated(%s)" % el.text)
                        keys_remaining = (el.text == 'true')

                    elif el.tag == '{%s}Contents' % self.namespace:
                        print("\t[process.Contents] %s:%s" % (el.tag,el.text))
                        print("\t[process]el.tag.key(%s)" % el.findtext('Key'))
                        marker = el.findtext('{%s}Key' % self.namespace)
                        yield marker[len(self.prefix):]
                        root.clear()
                '''

            except GeneratorExit:
                print("\t[exception] _list > GeneratorExit")
                # Need to read rest of response
                while True:
                    buf = resp.read(BUFSIZE)
                    # print("\t[process]buf(%s)" % buf)
                    if buf == '':
                        break
                break

            print("\t[process] _list > keys_remaining(%s)" % keys_remaining)
            if keys_remaining is None:
                continue
#                raise RuntimeError('Could not parse body')

    @retry
    def lookup(self, key):
        """Return metadata for given key"""

        log.debug('lookup(%s)', key)

        try:
            print("method: lookup._do_request")
            print("self.prefix: %s" % self.prefix)
            print("key: %s" % key)
            resp = self._do_request('HEAD', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except HTTPError as exc:
            print("lookup except: NoSuchObject")
            if exc.status == 404:
                raise NoSuchObject(key)
            else:
                raise

        return extractmeta(resp)

    @retry
    def get_size(self, key):
        '''Return size of object stored under *key*'''

        log.debug('get_size(%s)', key)

        try:
            print("method:get_size._do_request")
            resp = self._do_request('HEAD', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except HTTPError as exc:
            if exc.status == 404:
                raise NoSuchObject(key)
            else:
                raise

        for (name, val) in resp.getheaders():
            if name.lower() == 'content-length':
                return int(val)
        raise RuntimeError('HEAD request did not return Content-Length')


    @retry
    def open_read(self, key):
        """Open object for reading

        Return a file-like object. Data can be read using the `read` method. metadata is stored in
        its *metadata* attribute and can be modified by the caller at will. The object must be
        closed explicitly.
        """

        try:
            print("method:open_read._do_request")
            resp = self._do_request('GET', '/%s%s' % (self.prefix, key))
        except NoSuchKeyError:
            raise NoSuchObject(key)

        return ObjectR(key, resp, self, extractmeta(resp))

    def open_write(self, key, metadata=None, is_compressed=False):
        """Open object for writing

        `metadata` can be a dict of additional attributes to store with the object. Returns a file-
        like object. The object must be closed explicitly. After closing, the *get_obj_size* may be
        used to retrieve the size of the stored object (which may differ from the size of the
        written data).

        The *is_compressed* parameter indicates that the caller is going to write compressed data,
        and may be used to avoid recompression by the backend.

        Since Amazon S3 does not support chunked uploads, the entire data will
        be buffered in memory before upload.
        """
#        print ("[method]open_write key : %s" % key)
        log.debug('[method]open_write(%s): start', key)

        headers = dict()
        if metadata:
            for (hdr, val) in metadata.iteritems():
                headers['x-oss-meta-%s' % hdr] = val
                print("x-oss-meta-%s: %s" % (hdr,val))

        return ObjectW(key, self, headers)


    @retry
    def copy(self, src, dest):
        """Copy data stored under key `src` to key `dest`

        If `dest` already exists, it will be overwritten. The copying is done on
        the remote side.
        """

        log.debug('copy(%s, %s): start', src, dest)

        try:
            print("method: copy._do_request")
            resp = self._do_request('PUT', '/%s%s' % (self.prefix, dest),
                       headers={ 'x-oss-copy-source': '/%s/%s%s' % (self.bucket_name, self.prefix, src)})
            # Discard response body
            resp.read()
        except NoSuchKeyError:
            raise NoSuchObject(src)


    def _do_request(self, method, path, subres=None, query_string=None,
                    headers=None, body=None):
        if method == 'GET':
            print("[method]_do_request")
            print("[method]_do_request:params:method(%s)" % method)
            print("[method]_do_request:params:path(%s)" % path)
            print("[method]_do_request:params:subres(%s)" % subres)
            print("[method]_do_request:params:query_string(%s)" % query_string)
            print("[method]_do_request:params:headers(%s)" % headers)
            print("[method]_do_request:params:body(%s)" % body)
        '''
        '''
        '''Send request, read and return response object'''

        log.debug('_do_request(): start with parameters (%r, %r, %r, %r, %r, %r)',
                  method, path, subres, query_string, headers, body)

        if headers is None:
            headers = dict()

        headers['connection'] = 'keep-alive'

        if not body:
            headers['content-length'] = '0'

        redirect_count = 0
        while True:

            '''
            print("_do_request.(while true)")
            print("\t[process] method(%s)" % method)
            print("\t[process] path(%s)" % path)
            print("\t[process] headers(%s)" % headers)
            print("\t[process] subres(%s)" % subres)
            print("\t[process] query_string(%s)" % query_string)
            print("\t[process] body(%s)" % body)
            '''

            resp = self._send_request(method, path, headers, subres, query_string, body)
            # print("\t[process] resp : %s" % resp.read())
            log.debug("resp:%s" % resp.getheaders())
            log.debug('_do_request(): request-id: %s', resp.getheader('x-oss-request-id'))

            # print("\t[process] resp.status : %s" % resp.status)
            # print("\t[process] new_url: %s" % resp.getheader('Location'))
            if (resp.status < 300 or resp.status > 399 ):
                break

            # Assume redirect
            new_url = resp.getheader('Location')
            # print("resp: %s" % resp)
            # print("new_url: %s" % new_url)
            if new_url is None:
                break
            log.info('_do_request(): redirected to %s', new_url)

            redirect_count += 1
            if redirect_count > 10:
                raise RuntimeError('Too many chained redirections')

            # Pylint can't infer SplitResult Types
            #pylint: disable=E1103
            o = urlsplit(new_url)
            if o.scheme:
                if isinstance(self.conn, httplib.HTTPConnection) and o.scheme != 'http':
                    raise RuntimeError('Redirect to non-http URL')
                elif isinstance(self.conn, httplib.HTTPSConnection) and o.scheme != 'https':
                    raise RuntimeError('Redirect to non-https URL')
            if o.hostname != self.hostname or o.port != self.port:
                self.hostname = o.hostname
                self.port = o.port
                self.conn = self._get_conn()
            else:
                raise RuntimeError('Redirect to different path on same host')

            if body and not isinstance(body, bytes):
                body.seek(0)

            # Read and discard body
            log.debug('Response body: %s', resp.read())
#            print('\tResponse body: (%s)' % resp.read())

        # We need to call read() at least once for httplib to consider this
        # request finished, even if there is no response body.
        if resp.length == 0:
            resp.read()

        # Success 
        if resp.status >= 200 and resp.status <= 299:
            return resp

        # If method == HEAD, server must not return response body
        # even in case of errors
        if method.upper() == 'HEAD':
            raise HTTPError(resp.status, resp.reason)

        content_type = resp.getheader('Content-Type')

        if not content_type or not XML_CONTENT_RE.match(content_type):
            raise HTTPError(resp.status, resp.reason, resp.getheaders(), resp.read())

        tree = ElementTree.parse(resp).getroot()
        '''
        log.debug("#Start------------------------------")
        log.debug("Code :%s" % tree.findtext('Code'))
        log.debug("Message :%s" % tree.findtext('Message'))
        log.debug("HostId :%s" % tree.findtext('HostId'))
        log.debug("RequestId :%s" % tree.findtext('RequestId'))
        log.debug("OSSAccessKeyId :%s" % tree.findtext('OSSAccessKeyId'))
        log.debug("SignatureProvided :%s" % tree.findtext('SignatureProvided'))
        log.debug("StringToSign :%s" % tree.findtext('StringToSign'))
        log.debug("#End------------------------------")
        '''
        '''
        print("#Start------------------------------")
        print("Code :%s" % tree.findtext('Code'))
        print("Message :%s" % tree.findtext('Message'))
        print("HostId :%s" % tree.findtext('HostId'))
        print("RequestId :%s" % tree.findtext('RequestId'))
        print("OSSAccessKeyId :%s" % tree.findtext('OSSAccessKeyId'))
        print("SignatureProvided :%s" % tree.findtext('SignatureProvided'))
        print("StringToSign :%s" % tree.findtext('StringToSign'))
        print("#End------------------------------")
        '''

        raise get_S3Error(tree.findtext('Code'), tree.findtext('Message'))


    def clear(self):
        """Delete all objects in backend

        Note that this method may not be able to see (and therefore also not
        delete) recently uploaded objects.
        """

        # We have to cache keys, because otherwise we can't use the
        # http connection to delete keys.
        for (no, s3key) in enumerate(list(self)):
            if no != 0 and no % 1000 == 0:
                log.info('clear(): deleted %d objects so far..', no)

            log.debug('clear(): deleting key %s', s3key)

            # Ignore missing objects when clearing bucket
            self.delete(s3key, True)

    def __str__(self):
        return 'oss://%s/%s/%s' % (self.hostname, self.bucket_name, self.prefix)

    def _send_request(self, method, path, headers, subres=None, query_string=None, body=None):
        '''Add authentication and send request

        Note that *headers* is modified in-place. Returns the response object.
        '''

        # See http://docs.amazonwebservices.com/AmazonS3/latest/dev/RESTAuthentication.html
#-------------------------------------------------------------------------------
        log.debug("_send_request.path %s" % path)
        # print("[method]_send_request.path %s" % path)
        # Lowercase headers
        keys = list(headers.iterkeys())
        for key in keys:
            # print("\t[process]_send_request key : %s" % key)
            key_l = key.lower()
            if key_l == key:
                continue
            headers[key_l] = headers[key]
            del headers[key]

        # Date, can't use strftime because it's locale dependent

        params = dict()
#        m_get_date = time.mktime(time.localtime())
        m_get_date = time.time()
        # print("\t[process] m_get_date:%s" % m_get_date)
        if query_string:
            m_get_date_str = str(int(m_get_date))
            m_get_date_expires_str = str(int(m_get_date_str) + 60*5)
#            params['Date'] = m_get_date_expires_str
            params['Expires'] = m_get_date_expires_str
            params["OSSAccessKeyId"] = self.login
            headers['date'] = m_get_date_expires_str
        else:
            headers['date'] = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

        # print("\t[process] (%s)" % headers['date'])
        # print("\t[process]params(%s) " % params)

        '''
        [sign_str] 
            method + "\n" + 
            content_md5.strip() + "\n" + 
            content_type + "\n" + 
            date + "\n" + 
            canonicalized_oss_headers + canonicalized_resource
        '''
        auth_strs = [method, '\n']

        for hdr in ('content-md5', 'content-type', 'date'):
            if hdr in headers:
                auth_strs.append(headers[hdr])
            auth_strs.append('\n')

        for hdr in sorted(x for x in headers if x.startswith('x-oss-')):
            val = ' '.join(re.split(r'\s*\n\s*', headers[hdr].strip()))
            auth_strs.append('%s:%s\n' % (hdr, val))


        # Always include bucket name in path for signing
#         sign_path = urllib.quote('/%s%s' % (self.bucket_name, path))
        sign_path = '/%s%s' % (self.bucket_name, path)
#kei
#        sign_path = '%s' % (path)
        log.debug("sign_path : %s" % sign_path)
        auth_strs.append(sign_path)
        if subres:
            auth_strs.append('?%s' % subres)


        # False positive, hashlib *does* have sha1 member
        #pylint: disable=E1101

        signature = base64.encodestring(hmac.new(self.password, ''.join(auth_strs), sha).digest()).strip()

        if query_string:
            params["Signature"] = signature
        else:
            headers['Authorization'] = 'OSS %s:%s' % (self.login, signature)

        '''
        log.debug("auth_strs :%s" % auth_strs)
        for k in headers:
            log.debug("headers[%s] :%s" % (k,headers[k])) 
        log.debug("signature :%s" % signature) 
        log.debug("accessKey :%s" % self.password) 
        log.debug("sign_path :%s" % sign_path) 
        log.debug("<<<<<<<#end----------------")
        '''
        '''
        print("\tstart----------------")
        print("\tauth_strs :%s" % auth_strs)
        for k in headers:
            print("\theaders[%s] :%s" % (k,headers[k])) 
        print("\tsignature :%s" % signature) 
        print("\taccessKey :%s" % self.password) 
        print("\tsign_path :%s" % sign_path) 
        print("\tend----------------")
        '''


#-------------------------------------------------------------------------------
        # Construct full path
        if not self.hostname.startswith(self.bucket_name):
            path = '/%s%s' % (self.bucket_name, path)
        path = urllib.quote(path)
        if query_string:
            s = urllib.urlencode(query_string, doseq=True)
            if subres:
                path += '?%s&%s' % (subres, s)
            else:
                path += '?%s' % s
            p = urllib.urlencode(params, doseq=True)
            path += '&%s' % p 
        elif subres:
            path += '?%s' % subres
            p = urllib.urlencode(params, doseq=True)
            path += '&%s' % p 

        log.debug("path:%s" % path)
        log.debug("method:%s" % method)
        # print("path:%s" % path)
        # print("method:%s" % method)

        try:
            if body is None or not self.use_expect_100c or isinstance(body, bytes):
                # Easy case, small or no payload
                log.debug('_send_request(): processing request for %s', path)
                # print('_send_request(): processing request for %s' % path)
                # print('_send_request(): body %s' % body)
                self.conn.request(method, path, body, headers)
#debug
#                resp = self.conn.getresponse()
#                new_url = resp.getheader('Location')
#                print("[process] resp(%s)" % new_url)
#debug
                return self.conn.getresponse()

            # Potentially big message body, so we use 100-continue
            log.debug('_send_request(): sending request for %s', path)
            self.conn.putrequest(method, path)
            headers['expect'] = '100-continue'
            for (hdr, value) in headers.items():
                self.conn.putheader(hdr, value)
            self.conn.endheaders(None)

            log.debug('_send_request(): Waiting for 100-cont..')

            # Sneak in our own response class as instance variable,
            # so that it knows about the body that still needs to
            # be sent...
            resp = HTTPResponse(self.conn.sock, body)
            try:
                native_response_class = self.conn.response_class
                self.conn.response_class = resp
                assert self.conn.getresponse() is resp
            finally:
                self.conn.response_class = native_response_class
            return resp

        except:
            # We probably can't use the connection anymore
            # print("self.conn.close")
            self.conn.close()
            raise
    
    @retry
    def extractmeta(self, key):
        """Return metadata for given key"""

        log.debug('lookup(%s)', key)

        try:
            # print("method: extractmeta._do_request")
            resp = self._do_request('HEAD', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except HTTPError as exc:
            if exc.status == 404:
                raise NoSuchObject(key)
            else:
                raise

        return extractmeta(resp)


class HTTPResponse(httplib.HTTPResponse):
      '''
      This class provides a HTTP Response object that supports waiting for
      "100 Continue" and then sending the request body.

      The http.client.HTTPConnection module is almost impossible to extend,
      because the __response and __state variables are mangled. Implementing
      support for "100-Continue" doesn't fit into the existing state
      transitions.  Therefore, it has been implemented in a custom
      HTTPResponse class instead.

      Even though HTTPConnection allows to define a custom HTTPResponse class, it
      doesn't provide any means to pass extra information to the response
      constructor. Therefore, we instantiate the response manually and save the
      instance in the `response_class` attribute of the connection instance. We
      turn the class variable holding the response class into an instance variable
      holding the response instance. Only after the response instance has been
      created, we call the connection's `getresponse` method. Since this method
      doesn't know about the changed semantics of `response_class`, HTTPResponse
      instances have to fake an instantiation by just returning itself when called.
      '''

      def __init__(self, sock, body):
          self.sock = sock
          self.body = body
          self.__cached_status = None

      def __call__(self, sock, *a, **kw):
          '''Fake object instantiation'''

          assert self.sock is sock

          httplib.HTTPResponse.__init__(self, sock, *a, **kw)

          return self

      def _read_status(self):
          if self.__cached_status is None:
              self.__cached_status = httplib.HTTPResponse._read_status(self)

          return self.__cached_status

      def begin(self):
          log.debug('Waiting for 100-continue...')

          (version, status, reason) = self._read_status()
          if status != CONTINUE:
              # Oops, error. Let regular code take over
              return httplib.HTTPResponse.begin(self)

          # skip the header from the 100 response
          while True:
              skip = self.fp.readline(_MAXLINE + 1)
              if len(skip) > _MAXLINE:
                  raise LineTooLong("header line")
              skip = skip.strip()
              if not skip:
                  break
              log.debug('Got 100 continue header: %s', skip)

          # Send body
          if not hasattr(self.body, 'read'):
              self.sock.sendall(self.body)
          else:
              while True:
                  buf = self.body.read(BUFSIZE)
                  if not buf:
                      break
                  self.sock.sendall(buf)

          # *Now* read the actual response
          self.__cached_status = None
          return httplib.HTTPResponse.begin(self)

class ObjectR(object):
    '''An S3 object open for reading'''

    def __init__(self, key, resp, backend, metadata=None):
        self.key = key
        self.resp = resp
        self.md5_checked = False
        self.backend = backend
        self.metadata = metadata

        # False positive, hashlib *does* have md5 member
        #pylint: disable=E1101        
        self.md5 = hashlib.md5()

    def read(self, size=None):
        '''Read object data
        
        For integrity checking to work, this method has to be called until
        it returns an empty string, indicating that all data has been read
        (and verified).
        '''

        # chunked encoding handled by httplib
        buf = self.resp.read(size)

        # Check MD5 on EOF
        if not buf and not self.md5_checked:
            etag = self.resp.getheader('ETag').strip('"')
            self.md5_checked = True

            if not self.resp.isclosed():
                # http://bugs.python.org/issue15633
                ver = sys.version_info
                if ((ver > (2,7,3) and ver < (3,0,0))
                    or (ver > (3,2,3) and ver < (3,3,0))
                    or (ver > (3,3,0))):
                    # Should be fixed in these version
                    log.error('ObjectR.read(): response not closed after end of data, '
                              'please report on http://code.google.com/p/s3ql/issues/')
                    log.error('Method: %s, chunked: %s, read length: %s '
                              'response length: %s, chunk_left: %s, status: %d '
                              'reason "%s", version: %s, will_close: %s',
                              self.resp._method, self.resp.chunked, size, self.resp.length,
                              self.resp.chunk_left, self.resp.status, self.resp.reason,
                              self.resp.version, self.resp.will_close)

                self.resp.close() 

#             if etag != self.md5.hexdigest():
#                 log.warn('ObjectR(%s).close(): MD5 mismatch: %s vs %s', self.key, etag,
#                          self.md5.hexdigest())
#                 raise BadDigestError('BadDigest', 'ETag header does not agree with calculated MD5')

            return buf

        self.md5.update(buf)
        return buf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        '''Close object'''

        pass

class ObjectW(object):
    '''An S3 object open for writing

    All data is first cached in memory, upload only starts when
    the close() method is called.
    '''

    def __init__(self, key, backend, headers):
        self.key = key
        self.backend = backend
        self.headers = headers
        self.closed = False
        self.obj_size = 0
        self.fh = tempfile.TemporaryFile(bufsize=0) # no Python buffering

        # False positive, hashlib *does* have md5 member
        #pylint: disable=E1101        
        self.md5 = hashlib.md5()

    def write(self, buf):
        '''Write object data'''

        self.fh.write(buf)
        self.md5.update(buf)
        self.obj_size += len(buf)

    def is_temp_failure(self, exc):
        return self.backend.is_temp_failure(exc)

    @retry
    def close(self):
        '''Close object and upload data'''

        # Access to protected member ok
        #pylint: disable=W0212

        log.debug('ObjectW(%s).close(): start', self.key)

        self.closed = True
        self.headers['Content-Length'] = self.obj_size

        self.fh.seek(0)
        # print("method: close._do_request")
        resp = self.backend._do_request('PUT', '/%s%s' % (self.backend.prefix, self.key),
                                       headers=self.headers, body=self.fh)
        etag = resp.getheader('ETag').strip('"')
        assert resp.length == 0

#         if etag != self.md5.hexdigest():
        if etag is None or etag == "":
            log.warn('ObjectW(%s).close(): MD5 etag (%s)', self.key, etag)
#            log.warn('ObjectW(%s).close(): MD5 mismatch (%s vs %s)', self.key, etag, self.md5.hexdigest)
            try:
                self.backend.delete(self.key)
            except:
                log.exception('Objectw(%s).close(): unable to delete corrupted object!', self.key)
#            raise BadDigestError('BadDigest', 'Received ETag does not agree with our calculations.')

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def get_obj_size(self):
        if not self.closed:
            raise RuntimeError('Object must be closed first.')
        return self.obj_size


def get_S3Error(code, msg):
    '''Instantiate most specific S3Error subclass'''

    if code.endswith('Error'):
        name = code
    else:
        name = code + 'Error'
        class_ = globals().get(name, S3Error)

    if not issubclass(class_, S3Error):
        return S3Error(code, msg)

    return class_(code, msg)

def extractmeta(resp):
    '''Extract metadata from HTTP response object'''

    # Note: we implicitly rely on httplib to convert all headers to lower
    # case. because HTTP headers are case sensitive (so meta data field names
    # may have their capitalization changed). This only works, however, because
    # the meta data field names that we use are lower case as well. This problem
    # has only been solved cleanly in S3QL 2.0.
    
    meta = dict()
    for (name, val) in resp.getheaders():
        hit = re.match(r'^x-oss-meta-(.+)$', name)
        if not hit:
            continue
        meta[hit.group(1)] = val

    return meta

class HTTPError(Exception):
    '''
    Represents an HTTP error returned by S3.
    '''
    def __init__(self, status, msg, headers=None, body=None):
        super(HTTPError, self).__init__()
        self.status = status
        self.msg = msg
        self.headers = headers
        self.body = body
        self.retry_after = None

        if self.headers is not None:
            self._set_retry_after()

    def _set_retry_after(self):
        '''Parse headers for Retry-After value'''

        val = None
        for (k, v) in self.headers:
            if k.lower() == 'retry-after':
                hit = re.match(r'^\s*([0-9]+)\s*$', v)
                if hit:
                    val = int(v)
                else:
                    date = parsedate_tz(v)
                    if date is None:
                        log.warn('Unable to parse header: %s: %s', k, v)
                        continue
                    val = mktime_tz(*date) - time.time()

        if val is not None:
            if val > 300 or val < 0:
                log.warn('Ignoring invalid retry-after value of %.3f', val)
            else:
                self.retry_after = val

    def __str__(self):
        return '%d %s' % (self.status, self.msg)

class S3Error(Exception):
    '''
    Represents an error returned by S3. For possible codes, see
    http://docs.amazonwebservices.com/AmazonS3/latest/API/ErrorResponses.html
    '''
    def __init__(self, code, msg):
        super(S3Error, self).__init__(msg)
        self.code = code
        self.msg = msg
    def __str__(self):
        return '%s: %s' % (self.code, self.msg)

class NoSuchKeyError(S3Error): pass
class AccessDeniedError(S3Error, AuthorizationError): pass
class BadDigestError(S3Error): pass
class IncompleteBodyError(S3Error): pass
class InternalError(S3Error): pass
class InvalidAccessKeyIdError(S3Error, AuthenticationError): pass
class InvalidSecurityError(S3Error, AuthenticationError): pass
class SignatureDoesNotMatchError(S3Error, AuthenticationError): pass
class OperationAbortedError(S3Error): pass
class RequestTimeoutError(S3Error): pass
class TimeoutError(RequestTimeoutError): pass
class SlowDownError(S3Error): pass
class RequestTimeTooSkewedError(S3Error): pass
class NoSuchBucketError(S3Error, DanglingStorageURLError): pass
