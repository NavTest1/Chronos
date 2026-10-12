"""Write an explicit dataset file list and shallow offline-cache inventory."""
import argparse, csv, json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, required=True, help='Existing local .../data/singapore')
    p.add_argument('--cache-dir', type=Path, help='Optional .../artifacts/singapore; only top-level entries are listed')
    p.add_argument('--out-dir', type=Path, default=ROOT/'reports', help='Destination for small CSV/JSON files')
    args=p.parse_args(argv)
    data=args.data_dir.expanduser().resolve()
    if not data.is_dir():p.error('Data directory not found: '+str(data))
    output=args.out_dir.expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for file in sorted(data.rglob('*')):
        if file.is_file():
            rows.append({'relative_path':file.relative_to(data).as_posix(), 'size_bytes':file.stat().st_size,
                         'size_MiB':round(file.stat().st_size/1048576,3)})
    csv_path=output/'Chronos_Dataset_File_Inventory.csv'
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['relative_path','size_bytes','size_MiB'])
        writer.writeheader();writer.writerows(rows)
    cache=[]
    if args.cache_dir:
        target=args.cache_dir.expanduser().resolve()
        if target.is_dir():
            for item in sorted(target.iterdir()):
                cache.append({'name':item.name,'kind':'directory' if item.is_dir() else 'file',
                              'file_size_bytes':item.stat().st_size if item.is_file() else None})
        else:
            print('[INFO] Cache directory not found:',target, flush=True)
    cache_csv=output/'Chronos_OfflineCache_TopLevel.csv'
    with cache_csv.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['name','kind','file_size_bytes'])
        writer.writeheader();writer.writerows(cache)
    result={'status':'PASS','data_dir':str(data),'file_count':len(rows),
            'total_data_MiB':round(sum(int(x['size_bytes']) for x in rows)/1048576,2),
            'cache_top_level_count':len(cache),'dataset_inventory_csv':str(csv_path),
            'cache_inventory_csv':str(cache_csv),'generated_utc':datetime.now(timezone.utc).isoformat()}
    out=output/'Chronos_Inventory_Summary.json';out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('\n[CHRONOS DATA INVENTORY] PASS',flush=True)
    for k in ('file_count','total_data_MiB','cache_top_level_count','dataset_inventory_csv','cache_inventory_csv'):
        print(f'{k}: {result[k]}',flush=True)
    print('summary_json:',out,flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
