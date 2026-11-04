"""WinterWetter – schlanke, unabhängige Wetterarchiv-Anwendung.
Nur als Entwicklungs- und Testversion; keine amtliche Vor-Ort-Messstation.
"""
import calendar
import io
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, abort, jsonify, render_template_string, request, send_file
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from xml.etree import ElementTree as ET
from zipfile import ZipFile

TZ = ZoneInfo('Europe/Berlin')
BASE = Path(os.environ.get('WW_DATA_DIR', './data')).resolve()
BASE.mkdir(parents=True, exist_ok=True)
DB = BASE / 'winterwetter.sqlite3'
BACKUPS = BASE / 'backups'
BACKUPS.mkdir(exist_ok=True)
# Näherungswerte als konfigurierbare Startkoordinaten, keine amtlich vermessenen Gipfelpunkte.
PLACES = {
    'remscheid': {'name':'Remscheid', 'place':'Hohenhagen', 'lat':float(os.environ.get('REMSCHEID_LAT','51.165')), 'lon':float(os.environ.get('REMSCHEID_LON','7.204')), 'height':'378,86 m'},
    'wermelskirchen': {'name':'Wermelskirchen', 'place':'Rattenberg bei Buchholzen', 'lat':float(os.environ.get('WERMELSKIRCHEN_LAT','51.118')), 'lon':float(os.environ.get('WERMELSKIRCHEN_LON','7.266')), 'height':'ca. 345 m'}
}
URL = os.environ.get('WW_WEATHER_URL','https://api.open-meteo.com/v1/forecast')
DWD_URL = os.environ.get('WW_DWD_CAP_URL','https://opendata.dwd.de/weather/alerts/cap/COMMUNEUNION_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_DE.zip')
app = Flask(__name__)
LOCK = threading.Lock()

def con():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    with con() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS weather (
         city TEXT NOT NULL, forecast_hour TEXT NOT NULL, collected_at TEXT NOT NULL,
         temperature REAL, precipitation REAL, snowfall REAL, snow_depth REAL,
         soil_temperature REAL, weather_code INTEGER, source TEXT NOT NULL,
         PRIMARY KEY (city,forecast_hour,collected_at));
        CREATE TABLE IF NOT EXISTS warnings (
         identifier TEXT NOT NULL, sent TEXT NOT NULL, city TEXT NOT NULL,
         event TEXT, severity TEXT, valid_from TEXT, valid_to TEXT,
         headline TEXT, description TEXT, original_xml TEXT NOT NULL,
         collected_at TEXT NOT NULL, PRIMARY KEY(identifier,sent,city));
        CREATE TABLE IF NOT EXISTS runs (
         id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
         weather_count INTEGER, warning_count INTEGER, backup TEXT, errors TEXT);''')

def fetch(url, params=None):
    error=None
    for attempt in range(3):
        try:
            r=requests.get(url,params=params,timeout=25,headers={'User-Agent':'WinterWetterPro/0.1 (weather documentation)'})
            if r.status_code==429:
                wait=r.headers.get('Retry-After','5')
                try: delay=min(15,max(1,int(wait)))
                except ValueError: delay=5
                error=f'HTTP 429: Anfragebegrenzung ({url})'
                if attempt<2: time.sleep(delay); continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            error=str(e)
            if attempt<2: time.sleep(2**attempt)
    raise RuntimeError(error)

def collect_weather(city, location, collected):
    # Nur eine Anfrage je Stadt und Stunde, keine unnötigen parallelen Aufrufe.
    params={'latitude':location['lat'],'longitude':location['lon'],
        'hourly':'temperature_2m,precipitation,snowfall,snow_depth,soil_temperature_0cm,weather_code',
        'past_days':2,'forecast_days':1,'timezone':'Europe/Berlin'}
    payload=fetch(URL,params).json()
    if payload.get('error'): raise RuntimeError(str(payload.get('reason')))
    hourly=payload['hourly']; rows=[]
    for i,h in enumerate(hourly.get('time',[])):
        # Wettermodell-Zeitreihen bleiben mit ihrem tatsächlichen Abrufzeitpunkt versioniert.
        vals=[(hourly.get(k) or [None]*len(hourly['time']))[i] for k in
              ['temperature_2m','precipitation','snowfall','snow_depth','soil_temperature_0cm','weather_code']]
        rows.append((city,h,collected,*vals,URL))
    with con() as c:
        c.executemany('INSERT OR IGNORE INTO weather VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
    return len(rows)

def collect_dwd(collected):
    # DWD-CAP-Warnungen sind Warngebiete, nicht Messwerte an den Bergpunkten.
    import zipfile
    payload=fetch(DWD_URL).content
    saved=0
    with ZipFile(io.BytesIO(payload)) as archive:
        for filename in archive.namelist():
            if not filename.lower().endswith('.xml'): continue
            raw=archive.read(filename).decode('utf-8','replace')
            root=ET.fromstring(raw)
            def txt(elem,name):
                node=elem.find('{*}'+name)
                return ''.join(node.itertext()).strip() if node is not None else ''
            identifier=txt(root,'identifier'); sent=txt(root,'sent')
            for info in root.findall('{*}info'):
                # Statt ungenauer Ortsnamen-Zuordnung nur amtliche Gemeindenamen in areaDesc verwenden.
                areas=' | '.join(txt(a,'areaDesc') for a in info.findall('{*}area'))
                for city in PLACES:
                    if PLACES[city]['name'].lower() not in areas.lower(): continue
                    row=(identifier,sent,city,txt(info,'event'),txt(info,'severity'),
                         txt(info,'effective'),txt(info,'expires'),txt(info,'headline'),
                         txt(info,'description'),raw,collected)
                    with con() as c:
                        cur=c.execute('INSERT OR IGNORE INTO warnings VALUES (?,?,?,?,?,?,?,?,?,?,?)',row)
                        saved+=cur.rowcount
    return saved

def snapshot(label):
    dest=BACKUPS / f'backup-{label}.sqlite3'
    with con() as src, sqlite3.connect(dest) as tgt: src.backup(tgt)
    with sqlite3.connect(dest) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Backup integrity_check fehlgeschlagen')
    return str(dest)

def collect_all():
    if not LOCK.acquire(False): return {'error':'Datensammlung läuft bereits'}
    started=datetime.now(TZ).isoformat(); errors=[]; n=0; w=0; backup=''
    try:
        for city,location in PLACES.items():
            try: n+=collect_weather(city,location,started)
            except Exception as e: errors.append(f'{city}: {e}')
        try: w=collect_dwd(started)
        except Exception as e: errors.append(f'DWD CAP: {e}')
        try: backup=snapshot(datetime.now(TZ).strftime('%Y%m%d-%H%M%S'))
        except Exception as e: errors.append(f'Backup: {e}')
        with con() as c:
            c.execute('INSERT INTO runs(started_at,finished_at,weather_count,warning_count,backup,errors) VALUES(?,?,?,?,?,?)',
                (started,datetime.now(TZ).isoformat(),n,w,backup,json.dumps(errors,ensure_ascii=False)))
        return {'weather_rows_checked':n,'new_warning_versions':w,'backup':backup,'errors':errors,'started_at':started}
    finally: LOCK.release()

def report_rows(city,year,month):
    from collections import defaultdict
    start=f'{year:04}-{month:02}-01'
    end=f'{year+1:04}-01-01' if month==12 else f'{year:04}-{month+1:02}-01'
    with con() as c:
        # Letzte erfasste Version jedes Modell-Zeitpunktes, keine Mehrfachzählungen.
        values=c.execute('''SELECT w.* FROM weather w INNER JOIN
         (SELECT forecast_hour,MAX(collected_at) AS max_at FROM weather
          WHERE city=? AND forecast_hour>=? AND forecast_hour<? GROUP BY forecast_hour) v
         ON w.forecast_hour=v.forecast_hour AND w.collected_at=v.max_at
         WHERE w.city=? ORDER BY w.forecast_hour''',(city,start,end,city)).fetchall()
        warning=c.execute('SELECT * FROM warnings WHERE city=? AND (valid_to="" OR valid_to>=?) AND (valid_from="" OR valid_from<?) ORDER BY sent', (city,start,end)).fetchall()
    byday=defaultdict(list)
    for row in values: byday[row['forecast_hour'][:10]].append(row)
    result=[]
    for day in range(1,calendar.monthrange(year,month)[1]+1):
        ds=f'{year:04}-{month:02}-{day:02}'; vals=byday.get(ds,[])
        temps=[r['temperature'] for r in vals if r['temperature'] is not None]
        rains=[r['precipitation'] for r in vals if r['precipitation'] is not None]
        snow=[r['snowfall'] for r in vals if r['snowfall'] is not None]
        result.append((ds,min(temps) if temps else None,max(temps) if temps else None,
                       round(sum(rains),1) if rains else None,round(sum(snow),1) if snow else None,len(vals)))
    return result,warning

PAGE='''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WinterWetter Pro</title>
<style>body{font:16px system-ui;background:#f2f5f9;color:#12243a;margin:0}header{background:#17334e;color:white;padding:24px}main{max-width:1040px;margin:25px auto;padding:0 16px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}.card{background:white;border-radius:14px;padding:22px;box-shadow:0 2px 12px #0001}h1{margin:0}small{color:#607185}a{color:#125eac}button{padding:10px 18px;border:0;background:#175e99;color:white;border-radius:8px;cursor:pointer}table{border-collapse:collapse;width:100%;background:white}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}input,select{padding:9px}</style></head><body><header><h1>❄ WinterWetter Pro</h1><div>Winterdienst-Wetterarchiv | Thilo Garschagen Gartengestaltung</div></header><main><p><strong>Hinweis:</strong> Modellierte Wetterwerte, keine Messung am Gipfel. Die Standortkoordinaten sind vor produktiver Nutzung amtlich zu verifizieren. Warnungen gelten für DWD-Warngebiete, nicht automatisch für den konkreten Bergpunkt.</p>
<section class="cards">{% for key,p in places.items() %}<article class="card"><h2>{{p.name}}</h2><strong>{{p.place}}</strong><p>{{p.height}} über NN</p><small>Vorläufige Koordinaten: {{p.lat}}, {{p.lon}}</small><p><a href="/report/{{key}}?year={{year}}&month={{month}}">Monatsbericht als PDF</a></p></article>{% endfor %}</section>
<section class="card" style="margin-top:16px"><h2>Archivierungsstatus</h2>{% if run %}<p>Letzter Lauf: {{run.started_at}}</p><p>Wetter-Zeilen geprüft: {{run.weather_count}} | Neue Warnversionen: {{run.warning_count}}</p><p>Backup: {{'vorhanden' if run.backup else 'nicht bestätigt'}}</p><p>Fehler: {{run.errors}}</p>{% else %}<p>Noch keine Datensammlung ausgeführt.</p>{% endif %}<p><small>Automatischer Abruf jede Stunde, sofern der Server dauerhaft läuft. Manueller Abruf nur mit Admin-Schlüssel über die API.</small></p></section>
<section class="card" style="margin-top:16px"><h2>Berichtsmonat auswählen</h2><form action="/" method="get"><label>Jahr <input type="number" name="year" min="2020" max="2100" value="{{year}}"></label> <label>Monat <select name="month">{% for m in range(1,13) %}<option value="{{m}}" {{'selected' if m==month else ''}}>{{m}}</option>{% endfor %}</select></label> <button>Auswählen</button></form></section></main></body></html>'''

@app.get('/')
def home():
    now=datetime.now(TZ); year=int(request.args.get('year',now.year)); month=int(request.args.get('month',now.month))
    with con() as c: run=c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    return render_template_string(PAGE,places=PLACES,run=run,year=year,month=month)

@app.post('/api/collect')
def collect():
    key=os.getenv('WW_ADMIN_KEY')
    if not key or request.headers.get('X-Admin-Key') != key: abort(403)
    return jsonify(collect_all())

@app.get('/api/status')
def status():
    with con() as c: row=c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    if not row: return jsonify({'status':'not_started'})
    return jsonify(dict(row))

@app.get('/report/<city>')
def report(city):
    if city not in PLACES: abort(404)
    now=datetime.now(TZ)
    try: year=int(request.args.get('year',now.year)); month=int(request.args.get('month',now.month)); assert 1<=month<=12 and 2020<=year<=2100
    except (ValueError,AssertionError): abort(400)
    rows,warnings=report_rows(city,year,month)
    output=io.BytesIO(); doc=SimpleDocTemplate(output,pagesize=A4,rightMargin=30,leftMargin=30,topMargin=35,bottomMargin=35)
    styles=getSampleStyleSheet(); elements=[Paragraph(f'WinterWetter Pro – {PLACES[city]["name"]}',styles['Title']),
       Paragraph(f'{PLACES[city]["place"]} | {PLACES[city]["height"]} | {month:02}.{year}',styles['Heading2']),
       Paragraph('Standortbezogene Wettermodellwerte, keine direkten Stationsmessungen. Fehlende Angaben sind mit – markiert.',styles['Normal']),Spacer(1,16)]
    def fmt(x):return '–' if x is None else f'{x:.1f}'
    cells=[['Datum','Min °C','Max °C','Nied. mm','Schnee cm','Std.']]
    cells.extend([[d,fmt(mn),fmt(mx),fmt(pr),fmt(sn),str(n)] for d,mn,mx,pr,sn,n in rows])
    table=Table(cells,colWidths=[92,75,75,87,85,60],repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#d8e5ef')),('GRID',(0,0),(-1,-1),.3,colors.grey),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    elements.extend([table,Spacer(1,16),Paragraph('Archivierte DWD-Warnungen (Versionen)',styles['Heading2'])])
    if not warnings:elements.append(Paragraph('Keine archivierten Warnungen für diesen Zeitraum. Das bedeutet NICHT, dass es keine Warnungen gab.',styles['Normal']))
    for w in warnings:
        elements.append(Paragraph(f'{w["sent"]} – {w["event"] or "Warnung"} ({w["severity"] or "ohne Stufe"})',styles['Normal']))
        elements.append(Paragraph((w['headline'] or '')[:400].replace('&','&amp;').replace('<','&lt;'),styles['Normal']))
    elements.extend([Spacer(1,12),Paragraph('Wettermodellquelle: '+URL,styles['Normal']),Paragraph('Warnungsquelle: DWD CAP | '+DWD_URL,styles['Normal']),Paragraph('Bericht erzeugt: '+now.isoformat(),styles['Normal'])]);doc.build(elements);output.seek(0)
    return send_file(output,mimetype='application/pdf',as_attachment=True,download_name=f'WinterWetter-{city}-{year}-{month:02}.pdf')

init_db()
scheduler=BackgroundScheduler(timezone=TZ)
if os.environ.get('WW_ENABLE_SCHEDULER','0')=='1':
    scheduler.add_job(collect_all,'cron',minute=5,id='hourly',max_instances=1,coalesce=True)
    scheduler.start()
if __name__=='__main__':app.run(host='127.0.0.1',port=int(os.getenv('PORT','5000')))
