from lib import *
import sys
def mk(N=48, volk=3.0, wickf=0.6, dirn=1):
    def f(df):
        h,l,c,o,qv=(df[x].values for x in "h l c o qv".split())
        s=pd.Series
        lowN=s(l).shift(1).rolling(N).min().values; highN=s(h).shift(1).rolling(N).max().values
        vmed=s(qv).shift(1).rolling(288).median().values
        rng=h-l+1e-12
        lw=(np.minimum(o,c)-l)/rng; uw=(h-np.maximum(o,c))/rng
        longs=(l<lowN)&(c>lowN)&(qv>volk*vmed)&(lw>wickf)
        shorts=(h>highN)&(c<highN)&(qv>volk*vmed)&(uw>wickf)
        side=np.where(longs,1,-1)
        sig = longs if dirn==1 else shorts if dirn==-1 else (longs|shorts)
        return sig, side
    return f
for dirn in (1,-1):
  for N in (48,288):
    for volk in (3,6):
      for tp,sl in ((0.01,0.01),(0.015,0.03),(0.01,0.03)):
        run_all(mk(N,volk,0.6,dirn),tp,sl,48,label=f"dir{dirn} N{N} vk{volk} tp{tp} sl{sl}")
