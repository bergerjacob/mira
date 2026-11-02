import json, glob, re, time, sys
from nucleation import Schematic, TickSimulation, TickSettleMode
R='/home/bergerj/main/personal/minecraft-dev/mira/tests/fixtures/circuits/'
OFF=(0,0,0)
def match(actual, expected):
    def parse(s):
        m=re.match(r'([^\[]+)(?:\[(.*)\])?',s); props=dict(p.split('=') for p in m.group(2).split(',')) if m.group(2) else {}
        return m.group(1),props
    an,ap=parse(actual); en,ep=parse(expected)
    return an==en and all(ap.get(k)==v for k,v in ep.items())
def run(mode, lever_mode):
    res={}
    for f in sorted(glob.glob(R+'*.json')):
        d=json.load(open(f)); v=d['verification']; name=d['id']
        allb=d.get('support_blocks',[])+d['blocks']
        mn=[min(b[k] for b in allb) for k in 'xyz']
        global OFF
        OFF=(-mn[0],-mn[1],-mn[2])
        sc=Schematic.create(name)
        for b in allb:
            sc.set_block(b['x']+OFF[0],b['y']+OFF[1],b['z']+OFF[2],b['state'])
        sim=TickSimulation.from_schematic(sc, mode, 0,0,0, '')
        def chk(lst):
            out=[]
            for c in lst:
                p=c['pos']; a=sim.get_block(p[0]+OFF[0],p[1]+OFF[1],p[2]+OFF[2]); out.append((match(a,c['expected']),a,c['expected']))
            return out
        sim.run(2)
        init=chk(v.get('initial_checks',[]))
        for a in v.get('actions',[]):
            p=a['pos']; x,y,z=p[0]+OFF[0],p[1]+OFF[1],p[2]+OFF[2]
            if lever_mode=='use' and 'lever' in a['state']: sim.use_block(x,y,z)
            else: sim.place_block(x,y,z,a['state'])
        sim.run(v.get('tick_steps',20))
        fin=chk(v.get('final_checks',[]))
        ok=all(c[0] for c in init+fin)
        res[name]=(ok,[c for c in init+fin if not c[0]])
    return res
for mode in (TickSettleMode.Placement, TickSettleMode.InWorld):
  for lm in ('place','use'):
    r=run(mode,lm); print(mode, lm, sum(v[0] for v in r.values()),'/',len(r), {k:[(c[1][:40],c[2][:40]) for c in v[1]][:2] for k,v in r.items() if not v[0]})
