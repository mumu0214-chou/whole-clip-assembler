"""Read local sfnt names without adding font dependencies or downloading fonts."""
from mac_paths import path as _portable_path
from pathlib import Path
from hashlib import sha256
import struct
import shutil
import os
from functools import lru_cache

ROOT=Path(_portable_path(r'D:\DY video\整段组合器_V1.0.5\data\subtitle_fonts'))

def names(path):
    data=Path(path).read_bytes()
    u16=lambda o:struct.unpack_from('>H',data,o)[0]
    u32=lambda o:struct.unpack_from('>I',data,o)[0]
    offsets=[u32(12+i*4) for i in range(u32(8))] if data[:4]==b'ttcf' else [0]
    results=[]
    for face in offsets:
        name_offset=None
        for i in range(u16(face+4)):
            entry=face+12+i*16
            if data[entry:entry+4]==b'name': name_offset=u32(entry+8);break
        if name_offset is None:continue
        found=[]
        for i in range(u16(name_offset+2)):
            p=name_offset+6+i*12
            platform,encoding,language,nid,length,off=struct.unpack_from('>6H',data,p)
            if nid not in (1,16) or platform not in (0,3):continue
            start=name_offset+u16(name_offset+4)+off
            try:value=data[start:start+length].decode('utf-16-be').strip('\0')
            except UnicodeError:continue
            if value: found.append((language in (0x804,0x404),nid==16,value))
        if found:results.append(sorted(found,reverse=True)[0][2])
    return list(dict.fromkeys(results))

@lru_cache(maxsize=1)
def scan():
    paths=set()
    roots = [Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts',
             Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/Windows/Fonts',ROOT] if os.name == 'nt' else [
             Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home()/'Library/Fonts', ROOT]
    for root in roots:
        if root.is_dir():paths.update(p for p in root.iterdir() if p.suffix.lower() in ('.ttf','.otf','.ttc'))
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        _,value,_=winreg.EnumValue(key,i);p=Path(value)
                        if p.is_absolute() and p.is_file():paths.add(p)
            except OSError: pass
    except ImportError:pass
    result={}
    for p in sorted(paths):
        try:
            for family in names(p):
                result.setdefault(family,dict(family=family,path=str(p),imported=p.parent==ROOT))
        except (OSError,ValueError,struct.error,IndexError):pass
    # Standard aliases used by old configurations.
    if Path(_portable_path(r'C:\Windows\Fonts\msyh.ttc')).is_file():result.setdefault('Microsoft YaHei',dict(family='Microsoft YaHei',path=_portable_path(r'C:\Windows\Fonts\msyh.ttc'),imported=False))
    return sorted(result.values(),key=lambda x:(not any('\u4e00'<=c<='\u9fff' for c in x['family']),x['family']))

def import_font(path):
    path=Path(path)
    if path.suffix.lower() not in ('.ttf','.otf'):raise ValueError('仅支持导入 TTF / OTF 字体')
    families=names(path)
    if not families:raise ValueError('字体文件中没有有效字体名称')
    ROOT.mkdir(parents=True,exist_ok=True)
    digest=sha256(path.read_bytes()).hexdigest();target=ROOT/(digest+path.suffix.lower())
    if not target.exists():shutil.copyfile(path,target)
    if os.name=='nt':
        import ctypes
        ctypes.windll.gdi32.AddFontResourceExW(str(target),0x10,0)
    scan.cache_clear()
    return dict(font_family=families[0],font_file=str(target),font_sha256=digest)

def freeze(style):
    from subtitle_styles import normalize
    result=normalize(style)
    if result['font_file']:
        p=Path(result['font_file'])
        if not p.is_file():raise ValueError('字幕字体文件已丢失，请在编辑器重新选择字体：'+str(p))
        digest=sha256(p.read_bytes()).hexdigest()
        if result['font_sha256'] and result['font_sha256']!=digest:raise ValueError('字幕字体内容已改变，请重新导入字体')
        result['font_sha256']=digest
    elif result['font_family'] not in {f['family'] for f in scan()}:
        raise ValueError('系统字体已不存在，请重新选择：'+result['font_family'])
    return result

def media(style, paths):
    style=freeze(style)
    if style.get('font_file'):
        p=Path(style['font_file']);key=style['font_sha256']+p.suffix.lower();paths[key]=p
        return key
    return None
