import sys, json, time
sys.path.insert(0,'/home/bergerj/main/personal/minecraft-dev/mira')
from simulation.bridge import MinecraftBridge
S=json.load(open('/tmp/nuc_spike/clean.json')); b=MinecraftBridge(); b.connect(); c=b.run_command
c("gamerule sendCommandFeedback false"); n=0; ok=0
for name,d in S.items():
    n+=1; O=(90000+n*40,100,90000)
    c(f"forceload add {O[0]-5} {O[2]-5} {O[0]+30} {O[2]+30}"); c("tick unfreeze"); time.sleep(0.3)
    c(f"fill {O[0]-3} {O[1]-3} {O[2]-3} {O[0]+12} {O[1]+10} {O[2]+10} air")
    c("carpet fillUpdates false")
    for x,y,z,s in d['settled']: c(f"setblock {O[0]+x} {O[1]+y} {O[2]+z} {s}")
    c("carpet fillUpdates true"); c("tick freeze"); c("tick step 5")
    g=lambda: [c(f"mira_api get_state {O[0]+p[0]} {O[1]+p[1]} {O[2]+p[2]}") for p in d['probes']]
    pre=g(); x,y,z=d['act']; c(f"setblock {O[0]+x} {O[1]+y} {O[2]+z} minecraft:redstone_block"); c("tick step 30"); post=g(); c("tick unfreeze")
    print(name,'\n  live post',post,'\n  nuc  post',d['post'])
