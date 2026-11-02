import json
from nucleation import Schematic, TickSimulation, TickSettleMode
# each circuit: list of (x,y,z,state) placed bottom-up through engine, activation = place redstone_block at ACT, probes = positions to read
C={}
floor=lambda n:[(x,0,z,'minecraft:smooth_stone') for x in range(n) for z in range(3)]
C['wire_lamp']=dict(b=floor(8)+[(i,1,1,'minecraft:redstone_wire') for i in range(1,6)]+[(6,1,1,'minecraft:redstone_lamp[lit=false]')],act=(0,1,1),probes=[(1,1,1),(5,1,1),(6,1,1)])
C['not_gate_unused']=dict(b=floor(6)+[(1,1,1,'minecraft:smooth_stone'),(2,1,1,'minecraft:redstone_wall_torch[facing=east,lit=true]'),(3,1,1,'minecraft:smooth_stone'),(4,1,1,'minecraft:redstone_wire'),(5,1,1,'minecraft:redstone_lamp[lit=false]')],act=(0,1,1),probes=[(2,1,1),(5,1,1)])
C['repeater_lamp_unused']=dict(b=floor(8)+[(1,1,1,'minecraft:repeater[facing=west,delay=4]'),(2,1,1,'minecraft:redstone_wire'),(3,1,1,'minecraft:redstone_lamp[lit=false]')],act=(0,1,1),probes=[(1,1,1),(3,1,1)])
C['sticky_piston']=dict(b=floor(6)+[(1,1,1,'minecraft:sticky_piston[facing=east,extended=false]'),(2,1,1,'minecraft:stone')],act=(1,1,0),probes=[(1,1,1),(2,1,1),(3,1,1)])
C['or_two_inputs']=dict(b=floor(6)+[(1,1,0,'minecraft:redstone_wire'),(1,1,1,'minecraft:redstone_wire'),(1,1,2,'minecraft:redstone_wire'),(2,1,1,'minecraft:redstone_lamp[lit=false]')],act=(1,1,3-1+1) if False else (1,2,2),probes=[(2,1,1)])
out={}
for name,c in [(k,v) for k,v in C.items() if not k.endswith('_unused')]:
    sc=Schematic.create(name)
    for x,y,z,s in sorted(c['b'],key=lambda q:(q[1],q[0],q[2])): 
        try: sc.set_block(x,y,z,s+'{simulate=true}' if 'smooth_stone' not in s else s)
        except Exception as e: print('  settle failed',s,e); sc.set_block(x,y,z,s)
    settled=[(x,y,z,sc.get_block_string(x,y,z)) for x,y,z,_ in c['b']]
    sim=TickSimulation.from_schematic(sc,TickSettleMode.InWorld,0,0,0,''); sim.run(5)
    pre=[sim.get_block(*p) for p in c['probes']]
    sim.place_block(*c['act'],'minecraft:redstone_block'); sim.run(30)
    post=[sim.get_block(*p) for p in c['probes']]
    out[name]=dict(settled=settled,act=c['act'],probes=c['probes'],pre=pre,post=post)
    print(name,'\n  pre ',pre,'\n  post',post)
json.dump(out,open('clean.json','w'))
