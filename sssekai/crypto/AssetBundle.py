from typing import BinaryIO

SEKAI_AB_MAGIC = b'\x10\x00\x00\x00'

def decrypt_headaer_inplace(header : bytearray):
    assert len(header) == 128
    for i in range(0,128,8):       
        for j in range(5):
            header[i + j] = ~header[i + j] & 0xff
    return header

def decrypt(fin : BinaryIO, fout : BinaryIO) -> None:      
    magic = fin.read(4)    
    if magic == SEKAI_AB_MAGIC:
        fout.write(decrypt_headaer_inplace(bytearray(fin.read(128))))
        while (block := fin.read(65536)):
            fout.write(block)    
    else:
        fin.seek(0)
        while (block := fin.read(65536)):
            fout.write(block)

def encrypt(fin, fout):
    raise NotImplementedError
