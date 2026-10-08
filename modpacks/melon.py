import struct

ATTRIBUTE_NAME = "MelonInfoAttribute"
ATTRIBUTE_NAMESPACE = "MelonLoader"
MAX_SIZE = 64 * 1024 * 1024

TABLE_MODULE = 0x00
TABLE_TYPEREF = 0x01
TABLE_TYPEDEF = 0x02
TABLE_FIELDPTR = 0x03
TABLE_FIELD = 0x04
TABLE_METHODPTR = 0x05
TABLE_METHOD = 0x06
TABLE_PARAMPTR = 0x07
TABLE_PARAM = 0x08
TABLE_INTERFACEIMPL = 0x09
TABLE_MEMBERREF = 0x0A
TABLE_CONSTANT = 0x0B
TABLE_CUSTOMATTRIBUTE = 0x0C
TABLE_ASSEMBLY = 0x20

HAS_CUSTOM_ATTRIBUTE = (0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17, 0x14,
                        0x11, 0x1A, 0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2B)
RESOLUTION_SCOPE = (0x00, 0x1A, 0x23, 0x01)
TYPE_DEF_OR_REF = (0x02, 0x01, 0x1B)
MEMBER_REF_PARENT = (0x02, 0x01, 0x1A, 0x06, 0x1B)
HAS_CONSTANT = (0x04, 0x08, 0x17)
CUSTOM_ATTRIBUTE_TYPE = (None, None, 0x06, 0x0A, None)

class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def index(self, size):
        return self.u16() if size == 2 else self.u32()

    def packed(self):
        first = self.u8()
        if first & 0x80 == 0:
            return first
        if first & 0xC0 == 0x80:
            return ((first & 0x3F) << 8) | self.u8()
        if first & 0xE0 == 0xC0:
            rest = self.take(3)
            return ((first & 0x1F) << 24) | (rest[0] << 16) | (rest[1] << 8) | rest[2]
        raise ValueError("bad packed integer")

    def ser_string(self):
        if self.data[self.pos:self.pos + 1] == b"\xff":
            self.pos += 1
            return None
        return self.take(self.packed()).decode("utf-8", "replace")


def _sections(data, header):
    count = struct.unpack_from("<H", data, header + 6)[0]
    optional_size = struct.unpack_from("<H", data, header + 20)[0]
    optional = header + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic == 0x10B:
        directories = optional + 96
    elif magic == 0x20B:
        directories = optional + 112
    else:
        raise ValueError("not a PE image")
    table = optional + optional_size
    sections = []
    for i in range(count):
        base = table + 40 * i
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, base + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))
    cli_rva = struct.unpack_from("<I", data, directories + 14 * 8)[0]
    return sections, cli_rva


def _offset(sections, rva):
    for virtual_address, size, raw_pointer in sections:
        if virtual_address <= rva < virtual_address + size:
            return rva - virtual_address + raw_pointer
    raise ValueError("address outside the image")


def _streams(data, root):
    if struct.unpack_from("<I", data, root)[0] != 0x424A5342:
        raise ValueError("no metadata")
    length = struct.unpack_from("<I", data, root + 12)[0]
    pos = root + 16 + length + 2
    count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    streams = {}
    for _ in range(count):
        offset, size = struct.unpack_from("<II", data, pos)
        pos += 8
        end = data.index(b"\0", pos)
        name = data[pos:end].decode("ascii", "replace")
        pos = (end + 4) & ~3
        streams[name] = data[root + offset:root + offset + size]
    return streams


class Metadata:
    def __init__(self, data):
        if data[:2] != b"MZ":
            raise ValueError("not a PE image")
        header = struct.unpack_from("<I", data, 0x3C)[0]
        if data[header:header + 4] != b"PE\0\0":
            raise ValueError("not a PE image")
        sections, cli_rva = _sections(data, header)
        if not cli_rva:
            raise ValueError("not a .NET assembly")
        cli = _offset(sections, cli_rva)
        metadata_rva = struct.unpack_from("<I", data, cli + 8)[0]
        streams = _streams(data, _offset(sections, metadata_rva))
        self.strings = streams.get("#Strings", b"")
        self.blobs = streams.get("#Blob", b"")
        tables = streams.get("#~") or streams.get("#-")
        if tables is None:
            raise ValueError("no metadata tables")
        self._read_tables(tables)

    def _read_tables(self, tables):
        reader = Reader(tables)
        reader.take(6)
        heap_sizes = reader.u8()
        reader.u8()
        valid = struct.unpack("<Q", reader.take(8))[0]
        reader.take(8)
        self.rows = [0] * 64
        for table in range(64):
            if valid >> table & 1:
                self.rows[table] = reader.u32()
        if heap_sizes & 0x40:
            reader.u32()
        self.string_size = 4 if heap_sizes & 0x01 else 2
        self.guid_size = 4 if heap_sizes & 0x02 else 2
        self.blob_size = 4 if heap_sizes & 0x04 else 2
        self.layouts = self._layouts()
        self.starts = {}
        pos = reader.pos
        for table in range(TABLE_CUSTOMATTRIBUTE + 1):
            self.starts[table] = pos
            pos += self.rows[table] * sum(self.layouts[table])
        self.tables = tables

    def _simple(self, table):
        return 2 if self.rows[table] < 0x10000 else 4

    def _coded(self, tables):
        bits = (len(tables) - 1).bit_length()
        most = max(self.rows[t] for t in tables if t is not None)
        return 2 if most < 1 << (16 - bits) else 4

    def _layouts(self):
        s, g, b = self.string_size, self.guid_size, self.blob_size
        return {
            TABLE_MODULE: (2, s, g, g, g),
            TABLE_TYPEREF: (self._coded(RESOLUTION_SCOPE), s, s),
            TABLE_TYPEDEF: (4, s, s, self._coded(TYPE_DEF_OR_REF), self._simple(TABLE_FIELD),
                            self._simple(TABLE_METHOD)),
            TABLE_FIELDPTR: (self._simple(TABLE_FIELD),),
            TABLE_FIELD: (2, s, b),
            TABLE_METHODPTR: (self._simple(TABLE_METHOD),),
            TABLE_METHOD: (4, 2, 2, s, b, self._simple(TABLE_PARAM)),
            TABLE_PARAMPTR: (self._simple(TABLE_PARAM),),
            TABLE_PARAM: (2, 2, s),
            TABLE_INTERFACEIMPL: (self._simple(TABLE_TYPEDEF), self._coded(TYPE_DEF_OR_REF)),
            TABLE_MEMBERREF: (self._coded(MEMBER_REF_PARENT), s, b),
            TABLE_CONSTANT: (2, self._coded(HAS_CONSTANT), b),
            TABLE_CUSTOMATTRIBUTE: (self._coded(HAS_CUSTOM_ATTRIBUTE),
                                    self._coded(CUSTOM_ATTRIBUTE_TYPE), b),
        }

    def row(self, table, number):
        if not 1 <= number <= self.rows[table]:
            raise ValueError("row out of range")
        layout = self.layouts[table]
        reader = Reader(self.tables)
        reader.pos = self.starts[table] + (number - 1) * sum(layout)
        return [reader.index(size) for size in layout]

    def string(self, offset):
        end = self.strings.find(b"\0", offset)
        return self.strings[offset:end if end >= 0 else len(self.strings)].decode("utf-8", "replace")

    def blob(self, offset):
        reader = Reader(self.blobs)
        reader.pos = offset
        return reader.take(reader.packed())

    def type_name(self, table, number):
        if table == TABLE_TYPEREF:
            _, name, namespace = self.row(TABLE_TYPEREF, number)
        elif table == TABLE_TYPEDEF:
            _, name, namespace, _, _, _ = self.row(TABLE_TYPEDEF, number)
        else:
            return None, None
        return self.string(namespace), self.string(name)

    def constructor(self, coded):
        tag, number = coded & 0x7, coded >> 3
        if CUSTOM_ATTRIBUTE_TYPE[tag] == TABLE_MEMBERREF:
            parent, _, signature = self.row(TABLE_MEMBERREF, number)
            parent_table = MEMBER_REF_PARENT[parent & 0x7]
            return self.type_name(parent_table, parent >> 3), self.blob(signature)
        if CUSTOM_ATTRIBUTE_TYPE[tag] == TABLE_METHOD:
            _, _, _, _, signature, _ = self.row(TABLE_METHOD, number)
            owner = self._method_owner(number)
            return self.type_name(TABLE_TYPEDEF, owner), self.blob(signature)
        return (None, None), b""

    def _method_owner(self, method):
        owner = 0
        for number in range(1, self.rows[TABLE_TYPEDEF] + 1):
            if self.row(TABLE_TYPEDEF, number)[5] <= method:
                owner = number
        return owner

    def assembly_attributes(self):
        if not self.rows[TABLE_ASSEMBLY]:
            return
        assembly_tag = HAS_CUSTOM_ATTRIBUTE.index(TABLE_ASSEMBLY)
        for number in range(1, self.rows[TABLE_CUSTOMATTRIBUTE] + 1):
            parent, kind, value = self.row(TABLE_CUSTOMATTRIBUTE, number)
            if parent & 0x1F != assembly_tag or parent >> 5 != 1:
                continue
            yield self.constructor(kind), self.blob(value)


SIZED = {0x02: 1, 0x03: 2, 0x04: 1, 0x05: 1, 0x06: 2, 0x07: 2, 0x08: 4, 0x09: 4,
         0x0A: 8, 0x0B: 8, 0x0C: 4, 0x0D: 8}
INTEGERS = {0x04: "<b", 0x05: "<B", 0x06: "<h", 0x07: "<H", 0x08: "<i", 0x09: "<I",
            0x0A: "<q", 0x0B: "<Q"}


def _parameters(signature):
    reader = Reader(signature)
    reader.u8()
    count = reader.packed()
    if reader.u8() != 0x01:
        raise ValueError("constructor returns a value")
    kinds = []
    for _ in range(count):
        kind = reader.u8()
        if kind == 0x12:
            reader.packed()
            kinds.append("type")
        elif kind == 0x0E:
            kinds.append("string")
        elif kind in SIZED:
            kinds.append(kind)
        else:
            raise ValueError("unsupported parameter")
    return kinds


def _arguments(kinds, value):
    reader = Reader(value)
    if reader.u16() != 0x0001:
        raise ValueError("bad attribute prolog")
    values = []
    for kind in kinds:
        if kind in ("type", "string"):
            values.append((kind, reader.ser_string()))
        elif kind in INTEGERS:
            values.append(("int", struct.unpack(INTEGERS[kind], reader.take(SIZED[kind]))[0]))
        else:
            reader.take(SIZED[kind])
            values.append(("other", None))
    return values


def _info(values):
    strings = [v for k, v in values if k == "string"]
    numbers = [v for k, v in values if k == "int"]
    if not strings or not strings[0]:
        return None
    name = strings[0]
    if len(numbers) >= 3:
        rest = strings[1:]
        identifier = rest.pop(0) if len(rest) >= 2 and len(values) >= 8 else ""
        version = "%d.%d.%d" % tuple(numbers[:3])
        if identifier:
            version += "-" + identifier
        author = rest[0] if rest else ""
    else:
        version = strings[1] if len(strings) > 1 else ""
        author = strings[2] if len(strings) > 2 else ""
    return {"name": name.strip(), "version": (version or "").strip(), "author": (author or "").strip()}


def read_bytes(data):
    try:
        metadata = Metadata(data)
        for ((namespace, name), signature), value in metadata.assembly_attributes():
            if name == ATTRIBUTE_NAME and namespace == ATTRIBUTE_NAMESPACE:
                return _info(_arguments(_parameters(signature), value))
    except (ValueError, struct.error, IndexError, KeyError):
        return None
    return None


def read_info(path):
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_SIZE + 1)
    except OSError:
        return None
    if len(data) > MAX_SIZE:
        return None
    return read_bytes(data)
