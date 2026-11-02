import json, glob, re
from nucleation import Schematic, TickSimulation, TickSettleMode
R='/home/bergerj/main/personal/minecraft-dev/mira/tests/fixtures/circuits/'
live=json.load(open('live.json'))
def norm(s):
    s=s.strip()
    m=re.search(r'(minecraft:[a-z_]+(?:\[[^\]]*\])?)',s)
    return m.group(1) if m else s
def key(s):  # reduce to the behavioural bit
    for k in ('lit','extended','power','powered'):
        m=re.search(k+r'=(\w+)',s)
        if m: return f"{k}={m.group(1)}"
    return s
def nuc(d,mode):
    v=d['verification']; allb=d.get('support_blocks',[])+d['blocks']
    mn=[min(b[k] for b in allb) for k in 'xyz']
    sc=Schematic.create('x')
    for b in allb: sc.set_block(b['x']-mn[0],b['y']-mn[1],b['z']-mn[2],b['state'])
    sim=TickSimulation.from_schematic(sc,mode,0,0,0,''); sim.run(20)
    g=lambda lst:[key(sim.get_block(c['pos'][0]-mn[0],c['pos'][1]-mn[1],c['pos'][2]-mn[2])) for c in lst]
    init=g(v.get('initial_checks',[]))
    for a in v.get('actions',[]):
        p=a['pos']; sim.use_block(p[0]-mn[0],p[1]-mn[1],p[2]-mn[2])
    sim.run(20); return init,g(v.get('final_checks',[]))
print(f"{'fixture':20} {'expected':28} {'live,upd=off':26} {'live,upd=on':26} {'nuc Placement':26} {'nuc InWorld'}")
agree={'off':0,'on':0,'InWorld':0}; exp_ok={'off':0,'on':0,'nucP':0,'nucI':0}; N=0
for f in sorted(glob.glob(R+'*.json')):
    d=json.load(open(f)); v=d['verification']; name=d['id']; N+=1
    exp=([key(c['expected']) for c in v.get('initial_checks',[])],[key(c['expected']) for c in v.get('final_checks',[])])
    lo=live[name+'|fillUpdates=false']; ln=live[name+'|fillUpdates=true']
    L0=([key(norm(s)) for s in lo['init']],[key(norm(s)) for s in lo['final']])
    L1=([key(norm(s)) for s in ln['init']],[key(norm(s)) for s in ln['final']])
    NP=nuc(d,TickSettleMode.Placement); NI=nuc(d,TickSettleMode.InWorld)
    f_=lambda t:'/'.join(t[1])
    print(f"{name:20} {f_(exp):28} {f_(L0):26} {f_(L1):26} {f_(NP):26} {f_(NI)}")
    exp_ok['off']+=L0==exp; exp_ok['on']+=L1==exp; exp_ok['nucP']+=NP==exp; exp_ok['nucI']+=NI==exp
    agree['off']+=NP==L0; agree['on']+=NP==L1
print('matches fixture expectation (of',N,'):',exp_ok); print('Nucleation(Placement) == live:',agree)
