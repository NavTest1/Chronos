"""Create a check-summed, restorable processed-dataset ZIP for separate hosting.

Run only AFTER reviewing OSM/LTA redistribution conditions. The output archive
is NOT automatically published or added to git.
"""
from __future__ import annotations
import argparse, hashlib, json, zipfile
from pathlib import Path

REQUIRED = ('sg_travel_time.npy','sg_speed.npy','sg_edge_index.npy',
            'sg_edge_index_node_ids.npy','sg_node_ids.npy',
            'sg_edge_metadata.parquet','sg_time_buckets.csv','dataset_manifest.json')

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1<<20),b''):
            h.update(data)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--processed-dir',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--verify',action='store_true',help='Validate arrays before packaging')
    args=p.parse_args()
    source=args.processed_dir.resolve();dest=args.output.resolve()
    if dest.exists():raise FileExistsError('Will not overwrite '+str(dest))
    if dest.is_relative_to(source):raise ValueError('Place ZIP outside processed directory')
    missing=[x for x in REQUIRED if not (source/x).is_file()]
    if missing:raise FileNotFoundError('Processed dataset incomplete: '+repr(missing))
    if args.verify:
        from chronos.init.validate_dataset import validate_processed_dataset
        validate_processed_dataset(source)
    files=sorted(x for x in source.rglob('*') if x.is_file())
    dest.parent.mkdir(parents=True,exist_ok=True)
    records=[]
    try:
        with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for file in files:
                relative=file.relative_to(source)
                if any(p in ('.','..') for p in relative.parts) or file.is_symlink():
                    raise ValueError('Unexpected unsafe file '+str(file))
                member='data/singapore/processed/full/'+relative.as_posix()
                z.write(file,arcname=member)
                records.append({'path':member,'bytes':file.stat().st_size,'sha256':sha(file)})
        (dest.parent/(dest.name+'.sha256')).write_text(sha(dest)+'  '+dest.name+'\n',encoding='utf-8')
        (dest.parent/(dest.name+'.manifest.json')).write_text(json.dumps({'files':records},indent=2),encoding='utf-8')
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    print(json.dumps({'status':'PASS','archive':str(dest),'size_MiB':round(dest.stat().st_size/(1<<20),2),'sha256':sha(dest),'files':len(files)},indent=2))

if __name__=='__main__':main()
