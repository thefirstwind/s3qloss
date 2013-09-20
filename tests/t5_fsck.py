'''
t5_full.py - this file is part of S3QL (http://s3ql.googlecode.com)

Copyright (C) Nikolaus Rath <Nikolaus@rath.org>

This program can be distributed under the terms of the GNU GPLv3.
'''

from __future__ import division, print_function, absolute_import

from s3ql.common import get_backend_cachedir
from s3ql.database import Connection
import errno
import os.path
import shutil
import subprocess
import t4_fuse
from t4_fuse import populate_dir
import tempfile
import unittest2 as unittest

class FsckTests(t4_fuse.fuse_tests):

    def runTest(self):
        try:
            subprocess.call(['rsync', '--version'],
                            stderr=subprocess.STDOUT,
                            stdout=open('/dev/null', 'wb'))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise unittest.SkipTest('rsync not installed')
            raise

        ref_dir = tempfile.mkdtemp()
        try:
            populate_dir(ref_dir)

            # Make file system and fake high inode number
            self.mkfs()
            db = Connection(get_backend_cachedir(self.storage_url, self.cache_dir) + '.db')
            db.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?',
                       (2 ** 31 + 10, u'inodes'))
            db.close()

            # Copy source data
            self.mount()
            subprocess.check_call(['rsync', '-aHAX', ref_dir + '/',
                                   self.mnt_dir + '/'])
            self.umount()

            # Check that inode watermark is high
            db = Connection(get_backend_cachedir(self.storage_url, self.cache_dir) + '.db')
            self.assertGreater(db.get_val('SELECT seq FROM sqlite_sequence WHERE name=?', (u'inodes',)), 2 ** 31 + 10)
            self.assertGreater(db.get_val('SELECT MAX(id) FROM inodes'), 2 ** 31 + 10)
            db.close()

            # Renumber inodes
            self.fsck()

            # Check if renumbering was done
            db = Connection(get_backend_cachedir(self.storage_url, self.cache_dir) + '.db')
            self.assertLess(db.get_val('SELECT seq FROM sqlite_sequence WHERE name=?', (u'inodes',)), 2 ** 31)
            self.assertLess(db.get_val('SELECT MAX(id) FROM inodes'), 2 ** 31)
            db.close()

            # Compare
            self.mount()
            rsync = subprocess.Popen(['rsync', '-anciHAX', '--delete',
                                      '--exclude', '/lost+found',
                                      ref_dir + '/', self.mnt_dir + '/'],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            out = rsync.communicate()[0]
            if out:
                self.fail('Copy not equal to original, rsync says:\n' + out)
            elif rsync.returncode != 0:
                self.fail('rsync failed with ' + out)

            self.umount()
        finally:
            shutil.rmtree(ref_dir)


# Somehow important according to pyunit documentation
def suite():
    return unittest.makeSuite(FsckTests)

# Allow calling from command line
if __name__ == "__main__":
    unittest.main()