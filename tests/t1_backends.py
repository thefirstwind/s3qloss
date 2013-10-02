'''
t1_backends.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) 2008-2009 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import

from s3ql.backends import local, s3, gs, s3c, swift, rackspace, oss
from s3ql.backends.common import (ChecksumError, ObjectNotEncrypted, NoSuchObject,
    BetterBackend)
import ConfigParser
import os
import stat
import tempfile
import time
import unittest2 as unittest

class BackendTestsMixin(object):

    def newname(self):
        self.name_cnt += 1
        # Include special characters
        #return "s3ql_=/_%d" % self.name_cnt
        return "s3ql_t/_%d" % self.name_cnt

    #Test OK
#     def test_write(self):
#           
#          print(">>>>test_write:No1")
#          key = self.newname()
#          print("key: %s" % key)
#           
#          print(">>>>test_write:No2")
#          value = self.newname()
#          print("value: %s" % value)
#          metadata = { 'jimmy': 'jups@42' }
#      
#          print(">>>>test_write:No3")
#          self.assertRaises(NoSuchObject, self.backend.lookup, key)
#          print(">>>>test_write:No4")
#          self.assertRaises(NoSuchObject, self.backend.fetch, key)
#      
#          print(">>>>test_write:No5")
#          with self.backend.open_write(key, metadata) as fh:
#              fh.write(value)
#      
#          time.sleep(self.delay)
#           
#          print(">>>>test_write:No6")
#          with self.backend.open_read(key) as fh:
#              value2 = fh.read()
#           
#          print(">>>>test_write:No7")
#          self.assertEquals(value, value2)
#          print(">>>>test_write:No8")
#          self.assertEquals(metadata, fh.metadata)
#          print(">>>>test_write:No9")
#          self.assertEquals(self.backend[key], value)
#          print(">>>>test_write:No10")
#          self.assertEquals(self.backend.lookup(key), metadata)
#   
#     # Test OK
#     def test_setitem(self):
#         key = self.newname()
#         value = self.newname()
#         metadata = { 'jimmy': 'jups@42' }
#        
#         self.assertRaises(NoSuchObject, self.backend.lookup, key)
#         self.assertRaises(NoSuchObject, self.backend.__getitem__, key)
#        
#         with self.backend.open_write(key, metadata) as fh:
#             fh.write(self.newname())
#         time.sleep(self.delay)
#         self.backend[key] = value
#         time.sleep(self.delay)
#        
#         with self.backend.open_read(key) as fh:
#             value2 = fh.read()
#        
#         self.assertEquals(value, value2)
#         self.assertEquals(fh.metadata, dict())
#         self.assertEquals(self.backend.lookup(key), dict())
#     
#     # Test NG
#     def test_contains(self):
#         key = self.newname()
#         value = self.newname()
#       
#         self.assertFalse(key in self.backend)
#         self.backend[key] = value
#         time.sleep(self.delay)
#         self.assertTrue(key in self.backend)
#       
#     # Test OK
#     def test_delete(self):
#         key = self.newname()
#         value = self.newname()
#         self.backend[key] = value
#         time.sleep(self.delay)
#       
#         self.assertTrue(key in self.backend)
#         del self.backend[key]
#         time.sleep(self.delay)
#         self.assertFalse(key in self.backend)
#       
#      # Test NG
#     def test_clear(self):
#         key1 = self.newname()
#         key2 = self.newname()
#         self.backend[key1] = self.newname()
#         self.backend[key2] = self.newname()
#       
#         time.sleep(self.delay)
# # KEI TEST
#         self.assertEquals(len(list(self.backend)), 2)
#         self.backend.clear()
#         time.sleep(5*self.delay)
#         self.assertTrue(key1 not in self.backend)
#         self.assertTrue(key2 not in self.backend)
#         self.assertEquals(len(list(self.backend)), 0)
#    
#     # Test NG
#     def test_list(self):
#          
#         keys = [ self.newname() for dummy in range(12) ]
#         values = [ self.newname() for dummy in range(12) ]
#         for i in range(12):
#             self.backend[keys[i]] = values[i]
#          
#         time.sleep(self.delay)
#         self.assertEquals(sorted(self.backend.list()), sorted(keys))
#        
#     # Test NG
#     def test_copy(self):
#          
#         key1 = self.newname()
#         key2 = self.newname()
#         value = self.newname()
#           
#         print(">>>>> self.backend.lookup : %s" % self.backend.lookup)
#         print(">>>>test_copy No1 .key1: %s" % key1)
#         self.assertRaises(NoSuchObject, self.backend.lookup, key1)
#           
#         print(">>>>test_copy No2 .key2: %s" % key2)
#         self.assertRaises(NoSuchObject, self.backend.lookup, key2)
#          
#         print(">>>>test_copy No3 .value: %s" % value)
#         self.backend.store(key1, value)
#         time.sleep(self.delay)
#         self.backend.copy(key1, key2)
#          
#         time.sleep(self.delay)
#         self.assertEquals(self.backend[key2], value)
#     
#     # Test NG
#     def test_rename(self):
#      
#         key1 = self.newname()
#         key2 = self.newname()
#         value = self.newname()
#         self.assertRaises(NoSuchObject, self.backend.lookup, key1)
#         self.assertRaises(NoSuchObject, self.backend.lookup, key2)
#      
#         self.backend.store(key1, value)
#         time.sleep(self.delay)
#         self.backend.rename(key1, key2)
#      
#         time.sleep(self.delay)
#         self.assertEquals(self.backend[key2], value)
#         self.assertRaises(NoSuchObject, self.backend.lookup, key1)

# This test just takes too long (because we have to wait really long so that we don't
# get false errors due to propagation delays)
#@unittest.skip('takes too long')
class S3Tests(BackendTestsMixin, unittest.TestCase):
    def setUp(self):
        self.name_cnt = 0
        # This is the time in which we expect S3 changes to propagate. It may
        # be much longer for larger objects, but for tests this is usually enough.
        self.delay = 15

        self.backend = s3.Backend(*self.get_credentials('s3-test'), use_ssl=False)

    def tearDown(self):
        self.backend.clear()

    def get_credentials(self, name):

        authfile = os.path.expanduser('~/.s3ql/authinfo2')
        if not os.path.exists(authfile):
            self.skipTest('No authentication file found.')

        mode = os.stat(authfile).st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            self.skipTest("Authentication file has insecure permissions")

        config = ConfigParser.SafeConfigParser()
        config.read(authfile)

        try:
            fs_name = config.get(name, 'test-fs')
            backend_login = config.get(name, 'backend-login')
            backend_password = config.get(name, 'backend-password')
        except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
            self.skipTest("Authentication file does not have test section")

        return (fs_name, backend_login, backend_password)

class S3SSLTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 15
        self.backend = s3.Backend(*self.get_credentials('s3-test'), use_ssl=True)
        
class SwiftTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 0
        self.backend = swift.Backend(*self.get_credentials('swift-test'))

class RackspaceTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 0
        self.backend = rackspace.Backend(*self.get_credentials('rackspace-test'))

class GSTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 15
        self.backend = gs.Backend(*self.get_credentials('gs-test'), use_ssl=False)

class OSSTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 15
        self.backend = oss.Backend(*self.get_credentials('oss-test'), use_ssl=False)
        
class S3CTests(S3Tests):
    def setUp(self):
        self.name_cnt = 0
        self.delay = 0
        self.backend = s3c.Backend(*self.get_credentials('s3c-test'), use_ssl=False)   

class URLTests(unittest.TestCase):
    
    # access to protected members
    #pylint: disable=W0212
     
    def test_s3(self):
        self.assertEquals(s3.Backend._parse_storage_url('s3://name', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(s3.Backend._parse_storage_url('s3://name/', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(s3.Backend._parse_storage_url('s3://name/pref/', use_ssl=False)[2:],
                          ('name', 'pref/'))
        self.assertEquals(s3.Backend._parse_storage_url('s3://name//pref/', use_ssl=False)[2:],
                          ('name', '/pref/'))

    def test_gs(self):
        self.assertEquals(gs.Backend._parse_storage_url('gs://name', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(gs.Backend._parse_storage_url('gs://name/', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(gs.Backend._parse_storage_url('gs://name/pref/', use_ssl=False)[2:],
                          ('name', 'pref/'))
        self.assertEquals(gs.Backend._parse_storage_url('gs://name//pref/', use_ssl=False)[2:],
                          ('name', '/pref/'))
    def test_oss(self):
        self.assertEquals(oss.Backend._parse_storage_url('oss://name', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(oss.Backend._parse_storage_url('oss://name/', use_ssl=False)[2:],
                          ('name', ''))
        self.assertEquals(oss.Backend._parse_storage_url('oss://name/pref/', use_ssl=False)[2:],
                          ('name', 'pref/'))
        self.assertEquals(oss.Backend._parse_storage_url('oss://name//pref/', use_ssl=False)[2:],
                          ('name', '/pref/'))
                            
    def test_s3c(self):
        self.assertEquals(s3c.Backend._parse_storage_url('s3c://host.org/name', use_ssl=False),
                          ('host.org', 80, 'name', ''))
        self.assertEquals(s3c.Backend._parse_storage_url('s3c://host.org:23/name', use_ssl=False),
                          ('host.org', 23, 'name', ''))
        self.assertEquals(s3c.Backend._parse_storage_url('s3c://host.org/name/', use_ssl=False),
                          ('host.org', 80, 'name', ''))
        self.assertEquals(s3c.Backend._parse_storage_url('s3c://host.org/name/pref', use_ssl=False),
                          ('host.org', 80, 'name', 'pref'))
        self.assertEquals(s3c.Backend._parse_storage_url('s3c://host.org:17/name/pref/', use_ssl=False),
                          ('host.org', 17, 'name', 'pref/'))
                                
class LocalTests(BackendTestsMixin, unittest.TestCase):

    def setUp(self):
        self.name_cnt = 0
        self.backend_dir = tempfile.mkdtemp()
        self.backend = local.Backend('local://' + self.backend_dir, None, None)
        self.delay = 0

    def tearDown(self):
        self.backend.clear()
        os.rmdir(self.backend_dir)

class CompressionTests(BackendTestsMixin, unittest.TestCase):

    def setUp(self):
        self.name_cnt = 0
        self.backend_dir = tempfile.mkdtemp()
        self.plain_backend = local.Backend('local://' + self.backend_dir, None, None)
        self.backend = self._wrap_backend()
        self.delay = 0

    def _wrap_backend(self):
        return BetterBackend(None, 'zlib', self.plain_backend)

    def tearDown(self):
        self.backend.clear()
        os.rmdir(self.backend_dir)

class EncryptionTests(CompressionTests):

    def _wrap_backend(self):
        return BetterBackend('schlurz', None, self.plain_backend)

    def test_encryption(self):

        self.plain_backend['plain'] = b'foobar452'
        self.backend.store('encrypted', 'testdata', { 'tag': True })
        time.sleep(self.delay)
        self.assertEquals(self.backend['encrypted'], b'testdata')
        self.assertNotEquals(self.plain_backend['encrypted'], b'testdata')
        self.assertRaises(ObjectNotEncrypted, self.backend.fetch, 'plain')
        self.assertRaises(ObjectNotEncrypted, self.backend.lookup, 'plain')

        self.backend.passphrase = None
        self.assertRaises(ChecksumError, self.backend.fetch, 'encrypted')
        self.assertRaises(ChecksumError, self.backend.lookup, 'encrypted')

        self.backend.passphrase = 'jobzrul'
        self.assertRaises(ChecksumError, self.backend.fetch, 'encrypted')
        self.assertRaises(ChecksumError, self.backend.lookup, 'encrypted')


class EncryptionCompressionTests(EncryptionTests):

    def _wrap_backend(self):
        return BetterBackend('schlurz', 'zlib', self.plain_backend)


# Somehow important according to pyunit documentation
def suite():
    return unittest.makeSuite(LocalTests)


# Allow calling from command line
if __name__ == "__main__":
    unittest.main()
