from dataclasses import asdict
from valuation.case import import_legacy
from valuation import legacy

def sample(days=0):
 t=legacy.Terms(inst='RCPS',d_issue='2026-04-08',d_base='2026-06-30',d_mat='2036-04-07',
 S0=10432.,K0=59390.,issue_px=59390.,face_total=4999984710.,sig=.3259,par=500.,
 cpn=.01,div_basis=1,div_mode=0,ipay=12.,mat_mode=0,ytm=.07,ytm_cmp=1,
 p_mode='accrue',p_yield=.07,p_cmp=1,p_less_cpn=2,rfx_mode=0,issuer_call=0,k_w=0.,
 view='holder',base_shares=1630868.,dil_shares=473729.,gap_m=1.,grid_days=float(days),
 rf_curve=[[.25,.025],[1,.027],[5,.03],[10,.032],[50,.035]],
 cr_curve=[[.25,.09],[1,.095],[5,.10],[10,.11]])
 for k,d in [('cv_s','2026-04-09'),('cv_e','2036-04-07'),('p_s','2035-04-09'),('p_e','2036-04-06')]:
  setattr(t,k,legacy.date_to_months(t.d_issue, __import__('datetime').date.fromisoformat(d)))
 c=import_legacy(asdict(t),'Synthetic RCPS');c.exercise_styles={'p_f':'any','cv':'any'}
 return c
