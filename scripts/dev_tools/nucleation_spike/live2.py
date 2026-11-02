import sys, json, glob, time, re
sys.path.insert(0,'/home/bergerj/main/personal/minecraft-dev/mira')
from simulation.bridge import MinecraftBridge
R='/home/bergerj/main/personal/minecraft-dev/mira/tests/fixtures/circuits/'
S=json.load(open('/tmp/nuc_spike/settled.json'))
b=MinecraftBridge(); b.connect(); c=b.run_command
c("gamerule sendCommandFeedback false"); c("script resume")
def key(s):
    for k in ('lit','extended','power','powered'):
        m=re.search(k+r'[\'"]?[=:] ?(\w+)',s)
        if m: return f"{k}={m.group(1)}"
    return s
res={}; n=0
for f in sorted(glob.glob(R+'*.json')):
    d=json.load(open(f)); v=d['verification']; name=d['id']; n+=1
    O=(70000+n*40,100,70000)
    c(f"forceload add {O[0]-5} {O[2]-5} {O[0]+30} {O[2]+30}"); c("tick unfreeze"); time.sleep(0.3)
    c(f"fill {O[0]-3} {O[1]-3} {O[2]-3} {O[0]+10} {O[1]+10} {O[2]+10} air")
    c("carpet fillUpdates false")                       # literal paste, like Litematica-on-server w/ Carpet
    for q in S[name]['settled']:
        x,y,z=q['pos']; c(f"setblock {O[0]+x} {O[1]+y} {O[2]+z} {q['state']}")
    c("carpet fillUpdates true")
    c("tick freeze"); c("tick step 20")
    st=lambda lst:[key(c(f"mira_api get_state {O[0]+x['pos'][0]} {O[1]+x['pos'][1]} {O[2]+x['pos'][2]}")) for x in lst]
    init=st(v.get('initial_checks',[]))
    for a in v.get('actions',[]):
        p=a['pos']; c(f"mira_api toggle_lever {O[0]+p[0]} {O[1]+p[1]} {O[2]+p[2]}")
    c("tick step 20"); fin=st(v.get('final_checks',[])); c("tick unfreeze")
    res[name]=(init,fin)
ok=0
for k,(i,f_) in res.items():
    s=S[k]; m=(i==s['nuc_init'] and f_==s['nuc_final']); ok+=m
    print(f"{k:20} live={f_} nuc={s['nuc_final']} fixture_expects={s['exp_final']} {'MATCH' if m else 'MISMATCH'}")
print('live == nucleation:',ok,'/',len(res))
