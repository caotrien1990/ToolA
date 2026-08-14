import asyncio, tempfile
from pathlib import Path
from orchestrator.council.cron_jobs import CouncilCronService, CronJobError, next_schedule_at

def run(c): return asyncio.run(c)
def test_next_schedule_interval_and_cron():
 assert next_schedule_at('interval','1m',100)[1]==160
 assert next_schedule_at('cron','*/5 * * * *',100)[1]>100

def test_multiworker_crud_and_recovery():
 with tempfile.TemporaryDirectory() as d:
  svc=CouncilCronService(Path(d)/'cron.db',lambda:('pic','codo'),poll_seconds=.2)
  job=svc.create_job({'name':'audit','workers':['pic','codo'],'prompt':'x','schedule_type':'interval','schedule_value':'1m','enabled':True,'narrator_enabled':True,'narrator_worker':'pic','narrator_model':'deepseek-v4-pro'})
  assert job['workers']==['pic','codo'] and job['narrator_model']=='deepseek-v4-pro'
  updated=svc.update_job(job['id'],{'workers':['codo']}); assert updated['workers']==['codo']
  svc.delete_job(job['id']); assert svc.list_jobs()==[]

def test_service_dispatch_and_narrator():
 async def scenario():
  with tempfile.TemporaryDirectory() as d:
   svc=CouncilCronService(Path(d)/'cron.db',lambda:('pic','codo'),poll_seconds=.05)
   job=svc.create_job({'name':'audit','workers':['pic','codo'],'prompt':'x','schedule_type':'interval','schedule_value':'1m','narrator_enabled':True,'narrator_worker':'pic','narrator_model':'deepseek-v4-pro'})
   async def dispatch(j): return {'ok':True,'reply_text':'workers ok'}
   async def narrate(j,r): return {'ok':True,'reply_text':'narrated'}
   await svc.start(dispatch,narrate); runrow=await svc.trigger_job(job['id']); await asyncio.sleep(.1)
   got=svc.list_runs(job['id'],1)[0]; assert got['status']=='succeeded' and got['narrator_reply']=='narrated'; await svc.stop()
 run(scenario())
