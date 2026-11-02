import sys, json, glob, time
sys.path.insert(0,'/home/bergerj/main/personal/minecraft-dev/mira')
from simulation.bridge import MinecraftBridge
R='/home/bergerj/main/personal/minecraft-dev/mira/tests/fixtures/circuits/'
b=MinecraftBridge(); b.connect()
def cmd(c): return b.run_command(c)
cmd("gamerule sendCommandFeedback false")
out={}
n=0
for U in ('false','true'):
  for f in sorted(glob.glob(R+'*.json')):
    d=json.load(open(f)); v=d['verification']; name=d['id']
    n+=1; O=(30000+n*40,100,30000)
    cmd(f"forceload add {O[0]-5} {O[2]-5} {O[0]+30} {O[2]+30}")
    cmd(f"tick unfreeze"); time.sleep(0.2)
    cmd(f"fill {O[0]-3} {O[1]-3} {O[2]-3} {O[0]+10} {O[1]+10} {O[2]+10} air")
    cmd(f"carpet fillUpdates {U}")
    blocks=sorted(d.get('support_blocks',[])+d['blocks'], key=lambda q:(q['y'],q['x'],q['z']))
    for q in blocks: cmd(f"setblock {O[0]+q['x']} {O[1]+q['y']} {O[2]+q['z']} {q['state']}")
    cmd("carpet fillUpdates true")
    cmd("tick freeze"); cmd("tick step 20")
    def states(lst): return [cmd(f"mira_api get_state {O[0]+c['pos'][0]} {O[1]+c['pos'][1]} {O[2]+c['pos'][2]}") for c in lst]
    init=states(v.get('initial_checks',[]))
    for a in v.get('actions',[]):
        p=a['pos']; cmd(f"mira_api toggle_lever {O[0]+p[0]} {O[1]+p[1]} {O[2]+p[2]}")
    cmd("tick step 20")
    fin=states(v.get('final_checks',[]))
    out[f"{name}|fillUpdates={U}"]={'init':init,'final':fin}
    cmd("tick unfreeze")
cmd("carpet fillUpdates true")
json.dump(out,open('live.json','w'),indent=1)
print(len(out),'done')
