"""Bounded ZIP/RAR reader. Files are written by us, never by an extractor."""
import ctypes
import ctypes.util
import io
from pathlib import PurePosixPath
import re
import stat
import zipfile

MAX_UPLOAD = 60 * 1024 * 1024
MAX_BYTES = 120 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
MAX_FILES = 4000


def relative(name):
    name = str(name).replace('\\', '/')
    p = PurePosixPath(name)
    if (not name or name.startswith('/') or re.match(r'^[A-Za-z]:', name)
            or '..' in p.parts or any(':' in x or '\x00' in x for x in p.parts)
            or any(x.rstrip(' .') != x for x in p.parts)
            or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', x, re.I) for x in p.parts)):
        raise ValueError('Недопустимый путь архива: ' + name[:100])
    if not p.parts or len(name) > 240:
        raise ValueError('Пустой или слишком длинный путь')
    return p.as_posix()


def _rar(blob):
    libname = ctypes.util.find_library('archive')
    if not libname:
        raise ValueError('Для RAR нужна libarchive (Linux: libarchive13; macOS: brew install libarchive; Windows: archive.dll). ZIP работает без неё.')
    lib = ctypes.CDLL(libname)
    def bind(name, result, args):
        fn = getattr(lib, name); fn.restype = result; fn.argtypes = args
        return fn
    ptr = ctypes.c_void_p
    new = bind('archive_read_new', ptr, [])
    filters = bind('archive_read_support_filter_all', ctypes.c_int, [ptr])
    formats = bind('archive_read_support_format_all', ctypes.c_int, [ptr])
    open_mem = bind('archive_read_open_memory', ctypes.c_int, [ptr, ptr, ctypes.c_size_t])
    next_header = bind('archive_read_next_header', ctypes.c_int, [ptr, ctypes.POINTER(ptr)])
    pathname = bind('archive_entry_pathname', ctypes.c_char_p, [ptr])
    ftype = bind('archive_entry_filetype', ctypes.c_uint, [ptr])
    size = bind('archive_entry_size', ctypes.c_longlong, [ptr])
    symlink = bind('archive_entry_symlink', ctypes.c_char_p, [ptr])
    hardlink = bind('archive_entry_hardlink', ctypes.c_char_p, [ptr])
    read = bind('archive_read_data', ctypes.c_ssize_t, [ptr, ptr, ctypes.c_size_t])
    error = bind('archive_error_string', ctypes.c_char_p, [ptr])
    free = bind('archive_read_free', ctypes.c_int, [ptr])
    a = new(); buf = ctypes.create_string_buffer(blob)
    try:
        filters(a); formats(a)
        if open_mem(a, buf, len(blob)) < 0:
            raise ValueError('RAR не читается: ' + (error(a) or b'unknown').decode('utf-8', 'replace'))
        entry = ptr(); count = 0
        while True:
            rc = next_header(a, ctypes.byref(entry))
            if rc == 1:
                break
            if rc < 0:
                raise ValueError('Ошибка RAR: ' + (error(a) or b'unknown').decode('utf-8', 'replace'))
            count += 1
            if count > MAX_FILES:
                raise ValueError('В архиве больше %d элементов' % MAX_FILES)
            name = relative((pathname(entry) or b'').decode('utf-8', 'strict'))
            kind = ftype(entry)
            if symlink(entry) or hardlink(entry) or kind not in (stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Ссылки и специальные файлы в архиве запрещены: ' + name)
            if kind == stat.S_IFDIR:
                continue
            if size(entry) > MAX_FILE or size(entry) < 0:
                raise ValueError('Слишком большой файл: ' + name)
            chunks = []; n = 0; chunk = ctypes.create_string_buffer(65536)
            while True:
                got = read(a, chunk, len(chunk))
                if got < 0:
                    raise ValueError('Ошибка чтения RAR (возможно, архив зашифрован)')
                if not got:
                    break
                n += got
                if n > MAX_FILE:
                    raise ValueError('Превышен размер файла: ' + name)
                chunks.append(chunk.raw[:got])
            yield name, b''.join(chunks)
    finally:
        free(a)


def unpack(blob, filename, destination):
    if not blob or len(blob) > MAX_UPLOAD:
        raise ValueError('Размер архива должен быть от 1 байта до 60 МБ')
    def zip_entries():
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            if len(z.infolist()) > MAX_FILES:
                raise ValueError('В архиве больше %d элементов' % MAX_FILES)
            for entry in z.infolist():
                name = relative(entry.filename)
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Ссылки и специальные файлы запрещены: ' + name)
                if entry.flag_bits & 1:
                    raise ValueError('Архив защищён паролем')
                if entry.is_dir():
                    continue
                if entry.file_size > MAX_FILE:
                    raise ValueError('Слишком большой файл: ' + name)
                with z.open(entry) as f:
                    data = f.read(MAX_FILE + 1)
                yield name, data
    if filename.lower().endswith('.zip'):
        entries = zip_entries()
    elif filename.lower().endswith('.rar') and blob.startswith(b'Rar!\x1a\x07'):
        entries = _rar(blob)
    else:
        raise ValueError('Загрузите ZIP или RAR')
    total = 0; files = []; seen = set()
    for name, data in entries:
        if name.casefold() in seen:
            raise ValueError('Повторяющееся имя файла: ' + name)
        seen.add(name.casefold())
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_BYTES:
            raise ValueError('Превышен размер распакованного проекта (120 МБ)')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(name)
    if not files:
        raise ValueError('Архив не содержит файлов')
    return files, total
