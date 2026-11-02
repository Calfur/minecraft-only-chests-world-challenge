"""Typed NBT/Anvil editing for the offline challenge converter."""
import gzip,os,struct,zlib
from pathlib import Path
SECTOR=4096
class Tag:
    __slots__=('type','value')
    def __init__(self, kind, value): self.type,self.value=kind,value

class NBTReader:
    def __init__(self, data): self.b=memoryview(data); self.i=0
    def take(self,n):
        if n<0 or self.i+n>len(self.b): raise ValueError('Truncated NBT')
        b=self.b[self.i:self.i+n]; self.i+=n; return b
    def num(self,fmt): return struct.unpack('>'+fmt,self.take(struct.calcsize(fmt)))[0]
    def string(self):
        n=self.num('H');raw=bytes(self.take(n)).replace(b'\xc0\x80',b'\x00')
        text=raw.decode('utf-8',errors='surrogatepass')
        return text.encode('utf-16',errors='surrogatepass').decode('utf-16')
    def value(self,t):
        if t==1:return Tag(t,self.num('b'))
        if t==2:return Tag(t,self.num('h'))
        if t==3:return Tag(t,self.num('i'))
        if t==4:return Tag(t,self.num('q'))
        if t==5:return Tag(t,self.num('f'))
        if t==6:return Tag(t,self.num('d'))
        if t==7:return Tag(t,bytes(self.take(self.num('i'))))
        if t==8:return Tag(t,self.string())
        if t==9:
            et=self.num('b'); n=self.num('i')
            if n<0 or n>10_000_000: raise ValueError('Bad list length')
            return Tag(t,(et,[self.value(et).value for _ in range(n)]))
        if t==10:
            d={}
            while True:
                kind=self.num('b')
                if kind==0:break
                name=self.string();d[name]=self.value(kind)
            return Tag(t,d)
        if t in (11,12):
            n=self.num('i'); fmt='i' if t==11 else 'q'; size=4 if t==11 else 8
            if n<0:raise ValueError('Bad array size')
            return Tag(t,list(struct.unpack('>'+str(n)+fmt,self.take(n*size))))
        raise ValueError('Unknown NBT tag '+str(t))
    def root(self):
        t=self.num('b');name=self.string()
        if t!=10:raise ValueError('Not a compound NBT root')
        return name,self.value(t)

class NBTWriter:
    def __init__(self): self.b=bytearray()
    def num(self,fmt,x): self.b.extend(struct.pack('>'+fmt,x))
    def string(self,s):
        if s.isascii():
            b=s.encode('ascii').replace(b'\x00',b'\xc0\x80')
            self.num('H',len(b));self.b.extend(b);return
        units=s.encode('utf-16-be',errors='surrogatepass')
        text=''.join(chr(int.from_bytes(units[i:i+2],'big')) for i in range(0,len(units),2))
        b=text.encode('utf-8',errors='surrogatepass').replace(b'\x00',b'\xc0\x80')
        self.num('H',len(b));self.b.extend(b)
    def value(self,tag):
        t,v=tag.type,tag.value
        if t in (1,2,3,4,5,6):self.num({1:'b',2:'h',3:'i',4:'q',5:'f',6:'d'}[t],v)
        elif t==7:self.num('i',len(v));self.b.extend(v)
        elif t==8:self.string(v)
        elif t==9:
            et,items=v;self.num('b',et);self.num('i',len(items))
            for item in items:self.value(Tag(et,item))
        elif t==10:
            for name,sub in v.items():
                self.num('b',sub.type);self.string(name);self.value(sub)
            self.num('b',0)
        elif t in (11,12):
            self.num('i',len(v));fmt='i' if t==11 else 'q'
            self.b.extend(struct.pack('>'+str(len(v))+fmt,*v))
        else:raise ValueError('Unknown tag '+str(t))
    def root(self,name,tag):
        self.num('b',10);self.string(name);self.value(tag);return bytes(self.b)

def read_nbt(data):return NBTReader(data).root()
def write_nbt(root):return NBTWriter().root(*root)

def raw_chunks(path):
    raw=path.read_bytes()
    if len(raw)<8192:raise ValueError('Invalid region file '+str(path))
    chunks={}
    for slot in range(1024):
        word=int.from_bytes(raw[slot*4:slot*4+4],'big')
        off=word>>8;sectors=word&255
        if not off:continue
        begin=off*SECTOR
        if begin+5>len(raw):raise ValueError('Truncated region '+str(path))
        size=int.from_bytes(raw[begin:begin+4],'big')
        if begin<8192 or size<1 or size+4>sectors*SECTOR or begin+4+size>len(raw):raise ValueError('Bad chunk length in '+str(path))
        comp=raw[begin+4]
        if comp&0x80:raise ValueError('External chunk (.mcc) unsupported, region '+str(path))
        payload=raw[begin+5:begin+4+size]
        if comp==2:plain=zlib.decompress(payload)
        elif comp==1:plain=gzip.decompress(payload)
        elif comp==3:plain=payload
        else:raise ValueError(f'Unsupported chunk compression {comp} in {path}')
        chunks[slot]=read_nbt(plain)
    return chunks,raw[4096:8192]

def write_region(path,chunks,timestamps):
    header=bytearray(8192);header[4096:8192]=timestamps
    output=bytearray(header)
    for slot,root in sorted(chunks.items()):
        body=zlib.compress(write_nbt(root),level=4)
        payload=struct.pack('>I',len(body)+1)+b'\x02'+body
        sectors=(len(payload)+4095)//4096
        if sectors>255:raise ValueError('Chunk too big for region file')
        offset=len(output)//4096
        output[slot*4:slot*4+4]=(offset<<8|sectors).to_bytes(4,'big')
        output.extend(payload)
        output.extend(b'\0'*(sectors*4096-len(payload)))
    temp=path.with_suffix(path.suffix+'.working')
    try:
        temp.write_bytes(output)
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()
