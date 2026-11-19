import { sameSliceContext } from './slice-context.ts';
import type { Selector } from './logic.ts';
interface SnapshotIdentity {provider_id:string;revision:string;selector:Selector}
/** An opaque revision is immutable. Only a redundant notification for that exact source/slice is a no-op. */
export function canReuseSnapshot(active:SnapshotIdentity,next:SnapshotIdentity):boolean{
  return active.provider_id===next.provider_id&&active.revision===next.revision&&sameSliceContext({selector:active.selector,day:'',zone:''},{selector:next.selector,day:'',zone:''});
}
