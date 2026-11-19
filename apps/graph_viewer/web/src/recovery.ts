/** Rebuild only the selected graph slice. Expired ordinal actions are never retained or replayed. */
export class SnapshotRecovery {
  private pending:Promise<void>|null=null;
  run(bootstrap:()=>Promise<void>):Promise<void>{
    if(this.pending)return this.pending;
    let task:Promise<void>;try{task=bootstrap();}catch(error){return Promise.reject(error);}
    const pending=task.finally(()=>{if(this.pending===pending)this.pending=null;});this.pending=pending;return pending;
  }
}
