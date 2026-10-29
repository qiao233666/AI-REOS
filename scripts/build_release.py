"""按白名单构建可核对的公开源码包；不发布私人运行及审核材料。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

DIRECTORIES=('aireos','tests','examples','scripts','template','docs','.github')
ROOT_FILES=('README.md','AGENTS.md','pyproject.toml','.gitignore','.gitattributes','VERSION','CHANGELOG.md')
SUFFIXES={'.py','.md','.json','.toml','.yaml','.yml','.txt'}


def build(root,output,tag,validation=None):
    root,output=Path(root).resolve(),Path(output).resolve()
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?',tag):
        raise ValueError('发布标签不合法')
    files={}
    for folder in DIRECTORIES:
        for path in sorted((root/folder).rglob('*')):
            if not path.is_file():
                continue
            relative=path.relative_to(root)
            if path.is_symlink() or any(part.startswith('.') or part=='__pycache__' for part in relative.parts[1:]) or path.suffix not in SUFFIXES:
                continue
            files[relative.as_posix()]=path.read_bytes()
    for name in ROOT_FILES:
        if (root/name).is_file() and not (root/name).is_symlink():
            files[name]=(root/name).read_bytes()
    if validation:
        for path in sorted(Path(validation).rglob('*')):
            if path.is_file():
                if path.is_symlink() or path.suffix not in {'.md','.json','.log'}:
                    raise ValueError('公开验证目录含非预期文件')
                files['verification/'+path.relative_to(validation).as_posix()]=path.read_bytes()
    manifest=dict(tag=tag,files={name:hashlib.sha256(content).hexdigest() for name,content in sorted(files.items())},
        exclusions=['private verification history','runtime .aireos','research ledgers and data','legacy source (available in Git repository)'])
    output.mkdir(parents=True,exist_ok=False)
    payload=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    (output/'MANIFEST.json').write_bytes(payload)
    name='AI-REOS-'+tag+'-source.zip'
    with zipfile.ZipFile(output/name,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for filename,content in sorted({**files,'MANIFEST.json':payload}.items()):
            info=zipfile.ZipInfo(filename,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            archive.writestr(info,content)
    (output/'SHA256SUMS.txt').write_bytes((hashlib.sha256((output/name).read_bytes()).hexdigest()+'  '+name+'\n'+
        hashlib.sha256(payload).hexdigest()+'  MANIFEST.json\n').encode('utf-8'))
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--tag',default='v2.0.0-alpha.5')
    parser.add_argument('--validation',type=Path)
    args=parser.parse_args()
    manifest=build(Path(__file__).resolve().parents[1],args.output,args.tag,args.validation)
    print(json.dumps(dict(tag=args.tag,files=len(manifest['files']),output=str(args.output)),ensure_ascii=False))


if __name__=='__main__':
    main()
