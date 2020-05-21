from __future__ import division

import av

from .common import MethodLogger, TestCase, fate_suite
from .test_encode import assert_rgb_rotate, write_rgb_rotate


try:
    from cStringIO import StringIO
except ImportError:
    from io import BytesIO as StringIO


class NonSeekableBuffer:
    def __init__(self, data):
        self.data = data

    def read(self, n):
        data = self.data[0:n]
        self.data = self.data[n:]
        return data


CUSTOM_IO_PROTOCOL = 'pyavtest://'
CUSTOM_IO_FILENAME = 'custom_io_output.mpd'


class CustomIOLogger(object):
    """Log calls to open a file as well as method calls on the files"""
    def __init__(self, sandboxed):
        self._sandboxed = sandboxed
        self._log = []
        self._method_log = []

    def __call__(self, *args, **kwargs):
        self._log.append((args, kwargs))
        self._method_log.append(self.io_open(*args, **kwargs))
        return self._method_log[-1]

    def io_open(self, url, flags, options):
        # Remove the protocol prefix to reveal the local filename
        if CUSTOM_IO_PROTOCOL in url:
            url = url.split(CUSTOM_IO_PROTOCOL, 1)[1]

        if (flags & 3) == 3:
            mode = 'r+b'
            path = self._sandboxed(url)
        elif (flags & 1) == 1:
            mode = 'rb'
            path = url
        elif (flags & 2) == 2:
            mode = 'wb'
            path = self._sandboxed(url)
        else:
            raise RuntimeError("Unsupported io open mode {}".format(flags))

        return MethodLogger(open(path, mode))


class TestPythonIO(TestCase):

    def test_reading(self):

        with open(fate_suite('mpeg2/mpeg2_field_encoding.ts'), 'rb') as fh:
            wrapped = MethodLogger(fh)

            container = av.open(wrapped)

            self.assertEqual(container.format.name, 'mpegts')
            self.assertEqual(container.format.long_name, "MPEG-TS (MPEG-2 Transport Stream)")
            self.assertEqual(len(container.streams), 1)
            self.assertEqual(container.size, 800000)
            self.assertEqual(container.metadata, {})

            # Make sure it did actually call "read".
            reads = wrapped._filter('read')
            self.assertTrue(reads)

    def test_reading_no_seek(self):
        with open(fate_suite('mpeg2/mpeg2_field_encoding.ts'), 'rb') as fh:
            data = fh.read()

        buf = NonSeekableBuffer(data)
        wrapped = MethodLogger(buf)

        container = av.open(wrapped)

        self.assertEqual(container.format.name, 'mpegts')
        self.assertEqual(container.format.long_name, "MPEG-TS (MPEG-2 Transport Stream)")
        self.assertEqual(len(container.streams), 1)
        self.assertEqual(container.metadata, {})

        # Make sure it did actually call "read".
        reads = wrapped._filter('read')
        self.assertTrue(reads)

    def test_basic_errors(self):
        self.assertRaises(Exception, av.open, None)
        self.assertRaises(Exception, av.open, None, 'w')

    def test_writing(self):

        path = self.sandboxed('writing.mov')
        with open(path, 'wb') as fh:
            wrapped = MethodLogger(fh)

            output = av.open(wrapped, 'w', 'mov')
            write_rgb_rotate(output)
            output.close()
            fh.close()

            # Make sure it did actually write.
            writes = wrapped._filter('write')
            self.assertTrue(writes)

            # Standard assertions.
            assert_rgb_rotate(self, av.open(path))

    def test_buffer_read_write(self):

        buffer_ = StringIO()
        wrapped = MethodLogger(buffer_)
        write_rgb_rotate(av.open(wrapped, 'w', 'mp4'))

        # Make sure it did actually write.
        writes = wrapped._filter('write')
        self.assertTrue(writes)

        self.assertTrue(buffer_.tell())

        # Standard assertions.
        buffer_.seek(0)
        assert_rgb_rotate(self, av.open(buffer_))

    def test_writing_custom_io(self):

        # Custom I/O that opens file in the sandbox and logs calls
        wrapped_custom_io = CustomIOLogger(self.sandboxed)

        # Write a DASH package using the custom IO
        with av.open(CUSTOM_IO_PROTOCOL + CUSTOM_IO_FILENAME, 'w', io_open=wrapped_custom_io) as output:
            stream = output.add_stream('libx264', 24)
            stream.width = 100
            stream.height = 100
            stream.pix_fmt = "yuv420p"

            for i in range(5):
                frame = av.VideoFrame(stream.width, stream.height, 'rgb24')
                for packet in stream.encode(frame):
                    output.mux(packet)

            for packet in stream.encode(None):
                output.mux(packet)

        # Check that at least 3 files were opened using the custom IO:
        #   "CUSTOM_IO_FILENAME", init-stream0.m4s and chunk-stream0x.m4s
        self.assertGreaterEqual(len(wrapped_custom_io._log), 3)
        self.assertGreaterEqual(len(wrapped_custom_io._method_log), 3)

        # Check that all files were written to
        all_write = all(method_log._filter('write') for method_log in wrapped_custom_io._method_log)
        self.assertTrue(all_write)

        # Check that all files were closed
        all_closed = all(method_log._filter('close') for method_log in wrapped_custom_io._method_log)
        self.assertTrue(all_closed)
