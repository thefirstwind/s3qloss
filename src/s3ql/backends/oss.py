'''
backends/oss.py - this file is part of OSSFS (http://ossfs-oos.googlecode.com)

Copyright (C) Xing Xiaoning  <thefirstwind@gmail.com>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import
from . import s3c
from .common import NoSuchObject, retry
from .s3c import HTTPError,NoSuchKeyError , XML_CONTENT_RE, get_S3Error,ObjectW
from .s3c import C_DAY_NAMES,C_MONTH_NAMES,HTTPResponse,BadDigestError
from ..common import BUFSIZE,QuietError
import logging
#import base64 as b64encode
import base64 
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


# Pylint goes berserk with false positives
#pylint: disable=E1002,E1101,W0201

log = logging.getLogger("backends.oss")

class Backend(s3c.Backend):
    """A backend to stored data in Google Storage
    
    This class uses standard HTTP connections to connect to GS.
    
    The backend guarantees immediate get consistency and eventual list
    consistency.
    """

    use_expect_100c = True
    
    def __init__(self, storage_url, oss_key, oss_secret, use_ssl):
        super(Backend, self).__init__(storage_url, oss_key, oss_secret, use_ssl)
        
        self.namespace = 'http://doc.oss.aliyuncs.com'


    @staticmethod
    def _parse_storage_url(storage_url, use_ssl):
        hit = re.match(r'^oss://([^/]+)(?:/(.*))?$', storage_url)
        if not hit:
            raise QuietError('Invalid storage URL')

        bucket_name = hit.group(1)
        #hostname = '%s.oss-internal.aliyuncs.com' % bucket_name
        hostname = '%s.oss.aliyuncs.com' % bucket_name

        prefix = hit.group(2) or ''
        port = 443 if use_ssl else 80
        return (hostname, port, bucket_name, prefix)        

    def __str__(self):
        return 'oss://%s/%s' % (self.bucket_name, self.prefix)
    
    @retry
    def delete(self, key, force=False):
        '''Delete the specified object'''

        log.debug('delete(%s)', key)
        try:
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

        marker = ''
        waited = 0
        interval = 1 / 50
        iterator = self._list(prefix, marker)
        while True:
            try:
                marker = iterator.next()
                waited = 0
            except StopIteration:
                break
            except Exception as exc:
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
                yield marker

    def _list(self, prefix='', start=''):
        '''List keys in backend, starting with *start*

        Returns an iterator over all keys in the backend. This method
        does not retry on errors.
        '''

        keys_remaining = True
        marker = start
        prefix = self.prefix + prefix
 
        while keys_remaining:
            log.debug('list(%s): requesting with marker=%s', prefix, marker)
 
            keys_remaining = None
            resp = self._do_request('GET', '/', query_string={ 'prefix': prefix,
                                                              'marker': marker,
                                                              'max-keys': 1000 })
 
            if not XML_CONTENT_RE.match(resp.getheader('Content-Type')):
                raise RuntimeError('unexpected content type: %s' % resp.getheader('Content-Type'))
 
            itree = iter(ElementTree.iterparse(resp, events=("start", "end")))
            (event, root) = itree.next()
 
            log.debug("root.tag %",root.tag)
            
            namespace = re.sub(r'^\{(.+)\}.+$', r'\1', root.tag)
#kei--------
            if namespace != self.namespace:
                raise RuntimeError('Unsupported namespace: %s' % namespace)
 
            try:
                for (event, el) in itree:
                    if event != 'end':
                        continue
                    
                    if el.tag == 'ListBucketResult':
                        print("root.tag %", el.findtext('ListBucketResult'))

                    if el.tag == '{%s}IsTruncated' % self.namespace:
                        keys_remaining = (el.text == 'true')
 
                    elif el.tag == '{%s}Contents' % self.namespace:
                        marker = el.findtext('{%s}Key' % self.namespace)
                        yield marker[len(self.prefix):]
                        root.clear()
 
            except GeneratorExit:
                # Need to read rest of response
                while True:
                    buf = resp.read(BUFSIZE)
                    if buf == '':
                        break
                break

#kei--------------- 
            if keys_remaining is None:
                raise RuntimeError('Could not parse body')

    @retry
    def lookup(self, key):
        """Return metadata for given key"""

        log.debug('lookup(%s)', key)

        try:
            resp = self._do_request('HEAD', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except HTTPError as exc:
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

        log.debug('open_write(%s): start', key)

        headers = dict()
        if metadata:
            for (hdr, val) in metadata.iteritems():
                headers['x-oss-meta-%s' % hdr] = val

        return ObjectW(key, self, headers)


    @retry
    def copy(self, src, dest):
        """Copy data stored under key `src` to key `dest`
        
        If `dest` already exists, it will be overwritten. The copying is done on
        the remote side.
        """

        log.debug('copy(%s, %s): start', src, dest)

        try:
            resp = self._do_request('PUT', '/%s%s' % (self.prefix, dest),
                                    headers={ 'x-oss-copy-source': '/%s/%s%s' % (self.bucket_name,
                                                                           self.prefix, src)})
            # Discard response body
            resp.read()
        except NoSuchKeyError:
            raise NoSuchObject(src)


    def _do_request(self, method, path, subres=None, query_string=None,
                    headers=None, body=None):
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
                
            resp = self._send_request(method, path, headers, subres, query_string, body)
            log.debug('_do_request(): request-id: %s', resp.getheader('x-oss-request-id'))

            if (resp.status < 300 or resp.status > 399):
                break

            # Assume redirect
            new_url = resp.getheader('Location')
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
#keiwwwwwwwwwwwww 
        # Error
        tree = ElementTree.parse(resp).getroot()
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

    def _send_request(self, method, path, headers, subres=None, query_string=None, body=None):
        '''Add authentication and send request
        
        Note that *headers* is modified in-place. Returns the response object.
        '''

        # See http://docs.amazonwebservices.com/AmazonS3/latest/dev/RESTAuthentication.html

        # Lowercase headers
        keys = list(headers.iterkeys())
        for key in keys:
            key_l = key.lower()
            if key_l == key:
                continue
            headers[key_l] = headers[key]
            del headers[key]

        # Date, can't use strftime because it's locale dependent
        now = time.gmtime()
        date_str = ('%s, %02d %s %04d %02d:%02d:%02d GMT'
                           % (C_DAY_NAMES[now.tm_wday],
                              now.tm_mday,
                              C_MONTH_NAMES[now.tm_mon - 1],
                              now.tm_year, now.tm_hour,
                              now.tm_min, now.tm_sec))
        headers['Date'] = date_str
        send_time = str(int(time.time()))
        print("ddddddddddate: %s  -  %s:",date_str,send_time)
#         auth_strs = [method, '\n']
#         for hdr in ('content-md5', 'content-type', 'date'):
#             if hdr in headers:
#                 auth_strs.append(headers[hdr])
#             auth_strs.append('\n')
       
 #       
#         date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
#         headers['Date'] = date

#        auth_strs = [method, '\n']

#         for hdr in sorted(x for x in headers if x.startswith('x-oss-')):
#             val = ' '.join(re.split(r'\s*\n\s*', headers[hdr].strip()))
#             auth_strs.append('%s:%s\n' % (hdr, val))


        # Always include bucket name in path for signing
        sign_path = urllib.quote('/%s%s' % (self.bucket_name, path))
        signature_str = self._get_assign(method.strip().upper(), headers, sign_path)

        h = hmac.new(self.password, signature_str, hashlib.sha1)
        signature = base64.encodestring(h.digest()).strip()
#         auth_strs.append(sign_path)
        
        
        if subres:
            signature.append('?%s' % subres)

        # False positive, hashlib *does* have sha1 member
        #pylint: disable=E1101
#        signature = b64encode(hmac.new(self.password, ''.join(auth_strs), hashlib.sha1).digest())

        headers['Authorization'] = 'OSS %s:%s' % (self.login, signature)

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
        elif subres:
            path += '?%s' % subres

        try:
            if body is None or not self.use_expect_100c or isinstance(body, bytes):
                # Easy case, small or no payload
                log.debug('_send_request(): processing request for %s', path)
                self.conn.request(method, path, body, headers)
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
            self.conn.close()
            raise
    
    @retry
    def extractmeta(self, key):
        """Return metadata for given key"""

        log.debug('lookup(%s)', key)

        try:
            resp = self._do_request('HEAD', '/%s%s' % (self.prefix, key))
            assert resp.length == 0
        except HTTPError as exc:
            if exc.status == 404:
                raise NoSuchObject(key)
            else:
                raise

        return extractmeta(resp)
    
    def _get_assign(self, method, headers=None, sign_path="/", result=None):
        '''
        Create the authorization for OSS based on header input.
        You should put it into "Authorization" parameter of header.
        '''
        if not headers:
            headers = {}
        if not result:
            result = []
        content_md5 = self._safe_get_element('Content-MD5', headers)
        content_type = self._safe_get_element('Content-Type', headers)
        canonicalized_oss_headers = ""
        date = self._safe_get_element('Date', headers)
        canonicalized_resource = sign_path
        tmp_headers = self._format_header(headers)
        if len(tmp_headers) > 0:
            x_header_list = tmp_headers.keys()
            x_header_list.sort()
            for k in x_header_list:
                if k.startswith("x-oss-"):
                    canonicalized_oss_headers += k + ":" + tmp_headers[k] + "\n"
                    
        string_to_sign = method + "\n" + content_md5.strip() + "\n" + content_type + "\n";
        string_to_sign += date + "\n" + canonicalized_oss_headers + canonicalized_resource
        result.append(string_to_sign)
        
        log.debug("method: %s" % method)
        log.debug("content_md5: %s" % content_md5)
        log.debug("content_type: %s" % content_type)
        log.debug("date: %s" % date)
        log.debug("canonicalized_oss_headers: %s" % canonicalized_oss_headers)
        log.debug("canonicalized_resource: %s" % canonicalized_resource)
        log.debug("string_to_sign: %s" % string_to_sign)
        log.debug("string_to_sign_size: %s" % len(string_to_sign))

        return string_to_sign
    
    def _safe_get_element(self, name, container):
        for k, v in container.items():
            if k.strip().lower() == name.strip().lower():
                return v
        return ""
    
    def _format_header(self,headers=None):
        '''
        format the headers that self define
        convert the self define headers to lower.
        '''
        if not headers:
            headers = {}
        tmp_headers = {}
        for k in headers.keys():
            if isinstance(headers[k], unicode):
                headers[k] = headers[k].encode('utf-8')
    
            if k.lower().startswith("x-oss-"):
                k_lower = k.lower()
                tmp_headers[k_lower] = headers[k]
            else:
                tmp_headers[k] = headers[k]
        return tmp_headers
                
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
            
            if etag != self.md5.hexdigest():
                log.warn('ObjectR(%s).close(): MD5 mismatch: %s vs %s', self.key, etag,
                         self.md5.hexdigest())
                raise BadDigestError('BadDigest', 'ETag header does not agree with calculated MD5')
            
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
        resp = self.backend._do_request('PUT', '/%s%s' % (self.backend.prefix, self.key),
                                       headers=self.headers, body=self.fh)

#        etag = ""
###kei------
        etag = resp.getheader('ETag').strip('"')
        if resp is not None:
            etag = resp.getheader('ETag').strip('"')
            assert resp.length == 0

        if etag != self.md5.hexdigest():
            log.warn('ObjectW(%s).close(): MD5 mismatch (%s vs %s)', self.key, etag,
                     self.md5.hexdigest)
            try:
                self.backend.delete(self.key)
            except:
                log.exception('Objectw(%s).close(): unable to delete corrupted object!',
                              self.key)
#kei----------
            raise BadDigestError('BadDigest', 'Received ETag does not agree with our calculations.')

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def get_obj_size(self):
        if not self.closed:
            raise RuntimeError('Object must be closed first.')
        return self.obj_size
    
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