"""Prepared gzip files retain explicit compressed transfer lengths."""
import gzip
import shutil


def compress_binary(directory):
    for name in ['nodes.bin','edges.bin']:
        target=directory/(name+'.gz')
        with (directory/name).open('rb') as source,target.open('wb') as output:
            with gzip.GzipFile(filename='',fileobj=output,mode='wb',compresslevel=1,mtime=0) as compressed:
                shutil.copyfileobj(source,compressed,length=1024*1024)
        target.chmod(0o600)


def accepts_gzip(header):
    for part in header.split(','):
        values=[item.strip() for item in part.split(';')]
        if values[0].lower()!='gzip':
            continue
        try:
            quality=next((float(value[2:]) for value in values[1:] if value.startswith('q=')),1)
            return quality>0
        except ValueError:
            return False
    return False
