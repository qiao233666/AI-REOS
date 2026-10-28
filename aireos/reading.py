"""受管输出的累计字符统计；用于提醒，不冒充客户端token或强制费用额度。"""
import json
import sqlite3


def record(store,scope,packet,threshold=24000):
    path=store.home/'reading.sqlite3'
    connection=sqlite3.connect(path,timeout=15)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS readings(scope TEXT PRIMARY KEY,calls INTEGER,chars INTEGER)')
        connection.execute('BEGIN IMMEDIATE')
        old=connection.execute('SELECT calls,chars FROM readings WHERE scope=?',(scope,)).fetchone() or (0,0)
        size=0
        for _ in range(10):
            packet['reading']=dict(calls=old[0]+1,chars=old[1]+size,
                consider_handoff=old[1]+size>=threshold,unit='returned_characters',hard_cap=False)
            actual=len(json.dumps(packet,ensure_ascii=False))
            if actual==size:
                break
            size=actual
        connection.execute('INSERT OR REPLACE INTO readings VALUES(?,?,?)',(scope,old[0]+1,old[1]+size))
        connection.commit()
        return packet
    finally:
        connection.close()


def usage(store,scope='session'):
    path=store.home/'reading.sqlite3'
    if not path.exists():
        return dict(scope=scope,calls=0,chars=0,unit='returned_characters',hard_cap=False)
    connection=sqlite3.connect(path)
    try:
        row=connection.execute('SELECT calls,chars FROM readings WHERE scope=?',(scope,)).fetchone() or (0,0)
    finally:
        connection.close()
    return dict(scope=scope,calls=row[0],chars=row[1],unit='returned_characters',hard_cap=False)
