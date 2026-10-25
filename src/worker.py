"""Local WeRead cache discovery and export. No account credentials or AI."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
from xml.etree.ElementTree import ParseError
import export_core as core

DATA = Path.home() / 'Library/Containers/com.tencent.weread/Data'
SUPPORT = Path.home() / 'Library/Application Support/WeReadExporter'

class ExportError(Exception):
    pass

def read_paths(blob, fallback):
    paths=[]
    if blob:
        archive=plistlib.loads(blob)
        objects=archive.get('$objects',[])
        root=archive.get('$top',{}).get('root')
        if isinstance(root,plistlib.UID):
            node=objects[root.data]
            paths=[objects[p.data] for p in node.get('NS.objects',[]) if isinstance(p,plistlib.UID)]
    if not paths and fallback:paths=[fallback]
    result=[]
    for path in paths:
        if not isinstance(path,str):raise ExportError('目录路径格式不支持')
        url=urllib.parse.urlsplit(path)
        decoded=urllib.parse.unquote(url.path)
        p=PurePosixPath(decoded)
        if url.scheme or url.netloc or p.is_absolute() or '..' in p.parts or not (decoded.startswith('Text/') or len(p.parts)==1) or p.suffix.lower() not in ('.xhtml','.html'):
            raise ExportError('目录包含不支持的文件路径')
        result.append(decoded+('#'+url.fragment if url.fragment else ''))
    return result

def snapshot_query(account):
    source=DATA / 'Documents' / ('WeReadDB_'+account+'.db')
    if not source.is_file():raise ExportError('找不到微信读书目录数据库')
    with tempfile.TemporaryDirectory(prefix='weread-catalog-') as tmp:
        target=Path(tmp)/source.name
        tracked=[source,Path(str(source)+'-wal')]
        for attempt in range(3):
            before=[(p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else None for p in tracked]
            for p in tracked:
                dest=Path(tmp)/p.name
                if p.exists():shutil.copyfile(p,dest)
                elif dest.exists():dest.unlink()
            after=[(p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else None for p in tracked]
            if before==after:break
        else:raise ExportError('微信读书正在更新目录，请稍后重试')
        with closing(sqlite3.connect(target)) as db:
            db.execute('pragma query_only=on')
            books=db.execute('select bookId,title,author,isbn,publisher,chapterSize,format from WRBook').fetchall()
            columns={row[1] for row in db.execute('pragma table_info(WRChapter)')}
            uid='chapterUid' if 'chapterUid' in columns else 'NULL'
            chapters=db.execute('select bookId,chapterIdx,title,indentLevel,relativePath,relativePaths,'+uid+' from WRChapter order by chapterIdx').fetchall()
        return books,chapters

def discover():
    library=DATA/'Library'
    # Explicit directory read lets TCC denials reach the UI instead of showing an empty list.
    accounts=[p for p in library.iterdir() if p.is_dir() and p.name.isdigit() and (p/'Books').is_dir()]
    result=[]
    for account in accounts:
        bookdirs={p.name:p for p in (account/'Books').iterdir() if p.is_dir() and p.name.isdigit()}
        if not bookdirs:continue
        books,allchapters=snapshot_query(account.name)
        catalog={row[0]:row for row in books}
        chapters={}
        for bid,index,title,level,fallback,blob,uid in allchapters:
            if bid in bookdirs:
                is_txt=bid in catalog and catalog[bid][6]=='txt'
                source=None
                if is_txt:
                    paths=['Text/chapter-'+str(index)+'.xhtml'] if isinstance(uid,int) and uid>=0 else []
                    if paths:source=bid+'_'+str(uid)+'_o'
                else:
                    try:paths=read_paths(blob,fallback)
                    except Exception:paths=[]
                chapter={'index':index,'title':title or '未命名章节','level':max(1,level or 1),'paths':paths}
                if source:chapter['source']=source
                chapters.setdefault(bid,[]).append(chapter)
        for bid,folder in sorted(bookdirs.items()):
            row=catalog.get(bid)
            title=row[1] if row else '书籍 '+bid
            item={'id':account.name+':'+bid,'book_id':bid,'account':account.name,'title':title,'author':row[2] or '' if row else '', 'root':str(account/'Books'),'ready':False,'status':'等待目录下载','downloaded':0,'expected':0,'fingerprint':''}
            try:
                if not row:raise ExportError('等待目录下载')
                if row[6] not in ('epub','txt'):raise ExportError('暂不支持此书格式：'+str(row[6]))
                ch=chapters.get(bid,[])
                metadata={'title':title,'author':row[2] or '', 'isbn':row[3] or '', 'publisher':row[4] or '', 'chapters':ch}
                if row[6]=='txt':metadata['format']='txt'
                item['metadata']=metadata
                expected={p.split('#',1)[0] for c in ch for p in c['paths']}
                if row[6]=='txt':
                    actual={c['paths'][0] for c in ch if c['paths'] and (folder/c['source']).is_file()}
                    source_files=[folder/c['source'] for c in ch if c.get('source')]
                else:
                    actual=core.chapter_files(folder)
                    source_files=[folder/x for x in expected]
                item['downloaded']=len(actual & expected);item['expected']=len(expected)
                if not ch or any(not c['paths'] for c in ch) or (row[5] and len(ch)<row[5]):raise ExportError('等待完整目录下载')
                if expected!=actual:raise ExportError('正文下载未完成（'+str(item['downloaded'])+'/'+str(len(expected))+'）')
                h=hashlib.sha256(json.dumps(metadata,ensure_ascii=False,sort_keys=True).encode())
                for p in sorted(source_files+list((folder/'Styles').glob('*.css'))+list((folder/'css').glob('*.css'))+list((folder/'Images').glob('*'))):
                    if p.is_file():
                        stat=p.stat();h.update(str(p.relative_to(folder)).encode());h.update(str((stat.st_size,stat.st_mtime_ns)).encode())
                        if not stat.st_size:raise ExportError('正在下载正文文件')
                item['fingerprint']=h.hexdigest();item['ready']=True;item['status']='下载完成，可导出'
            except ExportError as error:item['status']=str(error)
            result.append(item)
    return result

def state_load(path):
    if not path.exists():return {}
    return json.loads(path.read_text(encoding='utf-8'))

def state_key(item,output):return item['id']+'|'+str(output)

def annotate(items,output,state):
    for item in items:
        saved=state.get(state_key(item,output),{})
        path=Path(saved.get('epub','/nonexistent-weread-export'))
        item['exported']=item['fingerprint']==saved.get('fingerprint') and path.is_file()
        item['epub']=str(path) if item['exported'] else ''
        if item['exported']:item['status']='已导出'
    return items

def export_one(identity,output,statefile,cache,seed):
    items=discover();item=next((i for i in items if i['id']==identity),None)
    if not item:raise ExportError('这本书已不在本机缓存中')
    if not item['ready']:raise ExportError(item['status'])
    state=state_load(statefile)
    annotate([item],output,state)
    if item['exported']:return {'skipped':True,'result':state[state_key(item,output)]}
    if seed.is_dir():
        for folder in seed.iterdir():
            if folder.is_dir():
                dest=cache/folder.name;dest.mkdir(parents=True,exist_ok=True)
                for p in folder.iterdir():
                    if p.is_file() and not (dest/p.name).exists():shutil.copyfile(p,dest/p.name)
    core.BOOKS={item['book_id']:item['metadata']}
    result=core.export_book(item['book_id'],Path(item['root']),output,cache,True)
    after=next((i for i in discover() if i['id']==identity),None)
    if not after or after['fingerprint']!=item['fingerprint']:
        Path(result['epub']).unlink()
        raise ExportError('下载内容发生变化，已取消本次导出，稍后将重试')
    result['fingerprint']=item['fingerprint'];result['exported_at']=time.time()
    state[state_key(item,output)]=result
    statefile.parent.mkdir(parents=True,exist_ok=True)
    temp=statefile.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(statefile)
    return {'skipped':False,'result':result}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['scan','export'])
    parser.add_argument('--id');parser.add_argument('--output',type=Path,default=Path.home()/'Downloads/微信读书导出')
    parser.add_argument('--state',type=Path,default=SUPPORT/'state.json')
    parser.add_argument('--cache',type=Path,default=SUPPORT/'images')
    parser.add_argument('--seed',type=Path,default=Path(__file__).parent/'image_seed')
    args=parser.parse_args()
    output=args.output.resolve()
    try:
        if args.action=='scan':response={'books':annotate(discover(),output,state_load(args.state))}
        else:
            args.state.parent.mkdir(parents=True,exist_ok=True)
            with (args.state.parent/'export.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                response=export_one(args.id,output,args.state,args.cache,args.seed)
        print(json.dumps({'ok':True,**response},ensure_ascii=False))
    except PermissionError:
        print(json.dumps({'ok':False,'kind':'permission','error':'需要允许“微信读书导出助手”读取微信读书缓存。请在系统设置 → 隐私与安全性 → 完全磁盘访问权限中添加此应用，然后退出并重新打开。'},ensure_ascii=False))
    except subprocess.CalledProcessError:
        print(json.dumps({'ok':False,'kind':'network','error':'配图下载失败，尚未保存 EPUB。请检查网络后重试。自动模式会稍后再次尝试。'},ensure_ascii=False))
    except (UnicodeDecodeError, ValueError, ParseError) as error:
        print(json.dumps({'ok':False,'kind':'format','error':'缓存未下载完成，或加密／文件格式暂不支持：'+str(error)},ensure_ascii=False))
    except sqlite3.DatabaseError:
        print(json.dumps({'ok':False,'kind':'catalog','error':'暂时无法读取微信读书目录，请稍后重试。'},ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'ok':False,'kind':'export','error':str(error)},ensure_ascii=False))

if __name__=='__main__':main()
