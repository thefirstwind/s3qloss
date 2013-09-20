'''
t2_inode_cache.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) 2008-2010 Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import

from s3ql import inode_cache
from s3ql.mkfs import init_tables
from s3ql.metadata import create_tables
from s3ql.database import Connection
import unittest2 as unittest
import time
import tempfile
import os

class cache_tests(unittest.TestCase):

    def setUp(self):
        # Destructors are not guaranteed to run, and we can't unlink
        # the file immediately because apsw refers to it by name. 
        # Therefore, we unlink the file manually in tearDown() 
        self.dbfile = tempfile.NamedTemporaryFile(delete=False)
        
        self.db = Connection(self.dbfile.name)
        create_tables(self.db)
        init_tables(self.db)
        self.cache = inode_cache.InodeCache(self.db, 0)

    def tearDown(self):
        self.cache.destroy()
        os.unlink(self.dbfile.name)

    def test_create(self):
        attrs = {'mode': 784,
                 'refcount': 3,
                 'uid': 7,
                 'gid': 2,
                 'size': 34674,
                 'rdev': 11,
                 'atime': time.time(),
                 'ctime': time.time(),
                 'mtime': time.time() }

        inode = self.cache.create_inode(**attrs)

        for key in attrs.keys():
            self.assertEqual(attrs[key], getattr(inode, key))

        self.assertTrue(self.db.has_val('SELECT 1 FROM inodes WHERE id=?', (inode.id,)))


    def test_del(self):
        attrs = {'mode': 784,
                'refcount': 3,
                'uid': 7,
                'gid': 2,
                'size': 34674,
                'rdev': 11,
                'atime': time.time(),
                'ctime': time.time(),
                'mtime': time.time() }
        inode = self.cache.create_inode(**attrs)
        del self.cache[inode.id]
        self.assertFalse(self.db.has_val('SELECT 1 FROM inodes WHERE id=?', (inode.id,)))
        self.assertRaises(KeyError, self.cache.__delitem__, inode.id)

    def test_get(self):
        attrs = {'mode': 784,
                'refcount': 3,
                'uid': 7,
                'gid': 2,
                'size': 34674,
                'rdev': 11,
                'atime': time.time(),
                'ctime': time.time(),
                'mtime': time.time() }

        inode = self.cache.create_inode(**attrs)
        for (key, val) in attrs.iteritems():
            self.assertEqual(getattr(inode, key), val)

        # Create another inode
        self.cache.create_inode(**attrs)

        self.db.execute('DELETE FROM inodes WHERE id=?', (inode.id,))
        # Entry should still be in cache
        self.assertEqual(inode, self.cache[inode.id])

        # Now it should be out of the cache
        for _ in xrange(inode_cache.CACHE_SIZE + 1):
            self.cache.create_inode(**attrs)

        self.assertRaises(KeyError, self.cache.__getitem__, inode.id)



def suite():
    return unittest.makeSuite(cache_tests)

if __name__ == "__main__":
    unittest.main()
