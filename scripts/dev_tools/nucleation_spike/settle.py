import json, glob, re
from nucleation import Schematic, TickSimulation, TickSettleMode
R='/home/bergerj/main/personal/minecraft-dev/mira/tests/fixtures/circuits/'
def key(s):
    for k in ('lit','extended','power','powered'):
        m=re.search(k+r'=(\w+)',s)
        if m: return f"{k}={m.group(1)}"
    return s
out={}
for f in sorted(glob.glob(R+'*.json')):
    d=json.load(open(f)); v=d['verification']; name=d['id']
    allb=sorted(d.get('support_blocks',[])+d['blocks'], key=lambda q:(q['y'],q['x'],q['z']))
    mn=[min(b[k] for b in allb) for k in 'xyz']
    sc=Schematic.create(name); 
    for b in allb:
        st=b['state']
        # place through engine so derived state (wire shape, torch, power) is resolved like a hand placement
        sc.set_block(b['x']-mn[0],b['y']-mn[1],b['z']-mn[2], st + '{simulate=true}')
    settled=[{'pos':[b['x'],b['y'],b['z']],'state':sc.get_block_string(b['x']-mn[0],b['y']-mn[1],b['z']-mn[2])} for b in allb]
    sim=TickSimulation.from_schematic(sc,TickSettleMode.InWorld,0,0,0,''); sim.run(20)
    g=lambda lst:[key(sim.get_block(c['pos'][0]-mn[0],c['pos'][1]-mn[1],c['pos'][2]-mn[2])) for c in lst]
    init=g(v.get('initial_checks',[]))
    for a in v.get('actions',[]):
        p=a['pos']; sim.use_block(p[0]-mn[0],p[1]-mn[1],p[2]-mn[2])
    sim.run(20); fin=g(v.get('final_checks',[]))
    out[name]={'settled':settled,'nuc_init':init,'nuc_final':fin,'exp_init':[key(c['expected']) for c in v.get('initial_checks',[])],'exp_final':[key(c['expected']) for c in v.get('final_checks',[])]}
json.dump(out,open('settled.json','w'),indent=1)
for k,v in out.items(): print(f"{k:20} settled-nuc={v['nuc_final']} expected={v['exp_final']} {'OK' if v['nuc_final']==v['exp_final'] and v['nuc_init']==v['exp_init'] else 'DIFF'}")
print(out['signal_split']['settled'])
