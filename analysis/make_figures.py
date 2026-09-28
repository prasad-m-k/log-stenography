import json, os, csv, matplotlib
matplotlib.use("Agg")
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES=os.path.join(ROOT,"results"); FIG=os.path.join(ROOT,"figures"); os.makedirs(FIG,exist_ok=True)
B=json.load(open(os.path.join(RES,"bytes.json")))
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
C=dict(pb="#1F4E5B",at="#4A7C7A",ag="#B8860B",gl="#E2E8F0",gd="#1A202C")
plt.rcParams.update({"font.family":"DejaVu Sans","font.size":9})
def save(fig,name):
    name=os.path.join(FIG,name)
    fig.patch.set_facecolor("white")
    fig.savefig(name+".png",dpi=300,bbox_inches="tight",facecolor="white")
    fig.savefig(name+".pdf",bbox_inches="tight",facecolor="white")
    from PIL import Image
    Image.open(name+".png").convert("RGB").save(name+".jpg",quality=95,dpi=(300,300))
# ---------- Fig 1 architecture
fig,ax=plt.subplots(figsize=(7.4,3.9)); ax.set_xlim(0,104); ax.set_ylim(0,56); ax.axis("off"); ax.set_facecolor("white")
def box(x,y,w,h,t,fc,tc="white",ls="-"):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.4,rounding_size=1.2",fc=fc,ec=C["gd"],lw=1,ls=ls))
    ax.text(x+w/2,y+h/2,t,ha="center",va="center",color=tc,fontsize=7.5)
def arr(x1,y1,x2,y2,ls="-"):
    ax.annotate("",xy=(x2,y2),xytext=(x1,y1),arrowprops=dict(arrowstyle="-|>",color=C["gd"],lw=1,ls=ls))
def lab(x,y,t,ha="center"): ax.text(x,y,t,ha=ha,va="center",fontsize=7,color=C["gd"],bbox=dict(fc="white",ec="none",pad=0.5))
ax.add_patch(FancyBboxPatch((1,27),28,21,boxstyle="round,pad=0.4",fc=C["gl"],ec=C["gd"],lw=0.8)); ax.text(15,46.3,"Build time",ha="center",fontsize=8,color=C["gd"])
ax.add_patch(FancyBboxPatch((36,1.5),30,46.5,boxstyle="round,pad=0.4",fc=C["gl"],ec=C["gd"],lw=0.8,ls="--")); ax.text(51,46.3,"Node (opaque to the app)",ha="center",fontsize=8,color=C["gd"])
box(3,37,24,6.5,"Log call sites\n(format strings, keys)",C["pb"])
box(3,29,24,5,"Brief: dictionary v=a",C["ag"])
box(3,7,24,9,"App + steno encoder\n(seq, template id, args)",C["pb"])
arr(15,37,15,34.3); arr(15,29,15,16.3); lab(15,22.5,"linked in")
box(38,31,26,8,"containerd / CRI-O\nappend",C["at"])
box(38,16,26,9,"/var/log/pods/.../0.log\n+41 B CRI prefix per line",C["at"])
box(38,4,26,6,"kubelet rotation, eviction",C["gl"],tc=C["gd"])
arr(27,11.5,37.6,35); lab(30.5,27.5,"stroke\non stdout")
arr(51,31,51,25.3); arr(51,10,51,15.6,ls=":")
box(73,31,28,8,"Log shipper\n(Fluent Bit tail)",C["pb"])
box(73,17.5,28,8,"Expander (de-stenographer):\nfilter or backend stage",C["ag"])
box(73,4,28,8,"Continuity watcher\n(gap check on seq)",C["at"])
arr(64.4,20.5,72.6,35); lab(68.5,24.5,"tail")
arr(87,31,87,25.9); arr(87,17.5,87,12.4)
ax.plot([27.4,31,31,103,103],[31.5,31.5,52.5,52.5,21.5],ls="--",color=C["gd"],lw=1)
arr(103,21.5,101.4,21.5,ls="--"); lab(67,52.5,"dictionary keyed by image digest, epoch a, published out of band")
save(fig,"fig1_arch"); plt.close(fig)
# ---------- Fig 2 analytic ceiling
b=np.linspace(20,600,300); p=41
fig,ax=plt.subplots(figsize=(6,3.2))
for c,ls,mk in [(2,"-","o"),(4,"--","s"),(8,"-.","^")]:
    k=(b+p)/(b/c+p); ax.plot(b,k,ls=ls,color=C["pb"],lw=1.4,marker=mk,markevery=40,ms=4,label=f"payload ratio c = {c}")
ax.plot(b,(b+p)/p,ls=":",color=C["gd"],lw=1.6,label="ceiling, c → ∞")
ax.set_xlabel("Original payload bytes per line, b"); ax.set_ylabel("On-disk reduction factor, k")
ax.grid(True,ls=":",color="#999",lw=0.5); ax.legend(frameon=False,fontsize=8); ax.set_facecolor("white"); ax.set_ylim(1,10)
ax.axvline(B[-1]["J"],color=C["ag"],lw=1); ax.text(206,8.9,"mean JSON line\nin this study (%d B)" % round(B[-1]["J"]) + "",fontsize=7,color=C["gd"],bbox=dict(fc="white",ec="none",pad=1))
save(fig,"fig2_ceiling"); plt.close(fig)
# ---------- Fig 3 measured
R=B[:-1]; P=41
names=[r["system"] for r in R]+["Pooled"]
kp=[r["k_disk_plain"] for r in R]+[B[-1]["k_disk_plain"]]
kj=[r["k_disk_json"] for r in R]+[B[-1]["k_disk_json"]]
x=np.arange(len(names)); w=0.38
fig,ax=plt.subplots(figsize=(6.6,3.2))
ax.bar(x-w/2,kp,w,color="white",ec=C["pb"],hatch="////",lw=1,label="vs. plaintext (app timestamp kept)")
ax.bar(x+w/2,kj,w,color=C["pb"],ec=C["gd"],lw=1,label="vs. JSON (CRI timestamp relied on)")
for i,(a,bb) in enumerate(zip(kp,kj)):
    ax.text(i-w/2,a+0.04,f"{a:.2f}",ha="center",fontsize=6.5); ax.text(i+w/2,bb+0.04,f"{bb:.2f}",ha="center",fontsize=6.5)
ax.set_xticks(x); ax.set_xticklabels(names,rotation=30,ha="right"); ax.axhline(1,color=C["gd"],lw=0.8)
ax.set_ylabel("On-disk reduction factor, k"); ax.set_ylim(0,3.5); ax.grid(True,axis="y",ls=":",color="#999",lw=0.5)
ax.legend(frameon=False,fontsize=7.5,loc="upper center",ncol=2); ax.set_facecolor("white")
save(fig,"fig3_measured"); plt.close(fig)


# ---------- Fig 4 emulator: loss vs event rate
rows=list(csv.DictReader(open(os.path.join(RES,"event_rate_sweep.csv"))))
Lj=B[-1]["J"]+P; Ls=B[-1]["S2"]+P; Rr=32e6
fig,(a1,a2)=plt.subplots(1,2,figsize=(7.4,3.1))
for enc,ls,mk,L,lab in [("json","-","o",Lj,"JSON"),("stroke","--","s",Ls,"stroke")]:
    rr=[r for r in rows if r["encoding"]==enc]
    x=[float(r["events_per_s"])/1000 for r in rr]; y=[100*float(r["loss_ratio_mean"]) for r in rr]
    e=[100*float(r["loss_ratio_sd"]) for r in rr]
    a1.errorbar(x,y,yerr=e,ls=ls,marker=mk,color=C["pb"] if enc=="json" else C["ag"],ms=4,capsize=2,label=lab+" (emulator)")
    xs=np.linspace(50,400,200); bound=[max(0,100*(1-Rr/(v*1000*L))) for v in xs]
    a1.plot(xs,bound,ls=":",color=C["gd"] if enc=="json" else C["ag"],lw=1)
    h=[float(r["escrow_held_max_MB_mean"])/1000 for r in rr]
    a2.plot(x,h,ls=ls,marker=mk,color=C["pb"] if enc=="json" else C["ag"],ms=4,label=lab)
a1.set_xlabel("Events per second (thousands)"); a1.set_ylabel("Lines lost (%)"); a1.set_title("(a) Loss, 90 s run, R = 32 MB/s",fontsize=9)
a1.grid(True,ls=":",color="#999",lw=0.5); a1.legend(frameon=False,fontsize=7.5); a1.text(52,45,"dotted lines: bound 1 - R/(rL)",fontsize=7)
a2.set_xlabel("Events per second (thousands)"); a2.set_ylabel("Peak bytes held by escrow (GB)")
a2.set_title("(b) Escrow held, no cap",fontsize=9); a2.grid(True,ls=":",color="#999",lw=0.5); a2.legend(frameon=False,fontsize=7.5)
fig.tight_layout(); save(fig,"fig4_event_rate"); plt.close(fig)
print("figures written to figures/")
