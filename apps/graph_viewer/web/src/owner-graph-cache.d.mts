import type {OwnerGraphCache} from './owner-graph-session.mjs';
import type {Selector} from './logic.ts';
export function ownerGraphCheckpointKey(options:{origin:string;stage:string|null;owner:string|null;provider:string;selector:Selector}):string|null;
export function ownerGraphCache(store:ReturnType<typeof browserOwnerTopologyStore>):OwnerGraphCache;
export function browserOwnerTopologyStore(factory?:IDBFactory):null|{change(fn:(current:unknown)=>unknown):Promise<unknown>};
