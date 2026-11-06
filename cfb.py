import struct, sys

class CFB:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        h = self.data[:512]
        assert h[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
        self.ssz = 1 << struct.unpack_from('<H', h, 30)[0]
        self.mssz = 1 << struct.unpack_from('<H', h, 32)[0]
        self.nfat = struct.unpack_from('<I', h, 44)[0]
        self.dirstart = struct.unpack_from('<I', h, 48)[0]
        self.cutoff = struct.unpack_from('<I', h, 56)[0]
        self.minifatstart = struct.unpack_from('<I', h, 60)[0]
        self.nminifat = struct.unpack_from('<I', h, 64)[0]
        self.difatstart = struct.unpack_from('<I', h, 68)[0]
        self.ndifat = struct.unpack_from('<I', h, 72)[0]
        # DIFAT
        difat = list(struct.unpack_from('<109I', h, 76))
        sec = self.difatstart
        while sec not in (0xFFFFFFFE, 0xFFFFFFFF) and self.ndifat:
            d = self.sector(sec)
            vals = struct.unpack('<%dI' % (self.ssz // 4), d)
            difat.extend(vals[:-1])
            sec = vals[-1]
        # FAT
        self.fat = []
        for s in difat[:self.nfat]:
            if s in (0xFFFFFFFE, 0xFFFFFFFF):
                continue
            self.fat.extend(struct.unpack('<%dI' % (self.ssz // 4), self.sector(s)))
        # MiniFAT
        self.minifat = []
        sec = self.minifatstart
        while sec not in (0xFFFFFFFE, 0xFFFFFFFF):
            self.minifat.extend(struct.unpack('<%dI' % (self.ssz // 4), self.sector(sec)))
            sec = self.fat[sec]
        self._read_dir()

    def sector(self, n):
        off = 512 + n * self.ssz
        return self.data[off:off + self.ssz]

    def chain(self, start):
        out = []
        s = start
        seen = set()
        while s not in (0xFFFFFFFE, 0xFFFFFFFF) and s < len(self.fat) and s not in seen:
            seen.add(s)
            out.append(s)
            s = self.fat[s]
        return out

    def read_stream_big(self, start, size):
        b = b''.join(self.sector(s) for s in self.chain(start))
        return b[:size]

    def _read_dir(self):
        dirdata = b''.join(self.sector(s) for s in self.chain(self.dirstart))
        self.entries = []
        for i in range(0, len(dirdata), 128):
            e = dirdata[i:i + 128]
            if len(e) < 128:
                break
            nlen = struct.unpack_from('<H', e, 64)[0]
            name = e[:max(0, nlen - 2)].decode('utf-16-le', 'replace')
            typ = e[66]
            start = struct.unpack_from('<I', e, 116)[0]
            size = struct.unpack_from('<Q', e, 120)[0]
            self.entries.append({'idx': i // 128, 'name': name, 'type': typ,
                                 'start': start, 'size': size,
                                 'left': struct.unpack_from('<I', e, 68)[0],
                                 'right': struct.unpack_from('<I', e, 72)[0],
                                 'child': struct.unpack_from('<I', e, 76)[0]})
        self.root = next(e for e in self.entries if e['type'] == 5)
        self.ministream = self.read_stream_big(self.root['start'], self.root['size'])

    def read_mini(self, start, size):
        out = b''
        s = start
        seen = set()
        while s not in (0xFFFFFFFE, 0xFFFFFFFF) and s < len(self.minifat) and s not in seen:
            seen.add(s)
            out += self.ministream[s * self.mssz:(s + 1) * self.mssz]
            s = self.minifat[s]
        return out[:size]

    def streams(self):
        res = {}
        for e in self.entries:
            if e['type'] != 2:
                continue
            if e['size'] < self.cutoff:
                res[e['name']] = self.read_mini(e['start'], e['size'])
            else:
                res[e['name']] = self.read_stream_big(e['start'], e['size'])
        return res


if __name__ == '__main__':
    c = CFB(sys.argv[1])
    print('sector', c.ssz, 'mini', c.mssz, 'cutoff', c.cutoff)
    for e in c.entries:
        print(e['idx'], repr(e['name']), 'type', e['type'], 'size', e['size'], 'child', e['child'])
