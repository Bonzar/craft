import type {Delta} from './contracts.ts';
export interface OwnerPlacement{scope:string;epoch:string;ids:(string|null)[];positions:Float32Array}
export function ownerGraphDeltaIds(current:(string|null)[],delta:Delta):(string|null)[];
export function extendOwnerGraphPositions(previous:OwnerPlacement|null,next:OwnerPlacement&{edges:Uint32Array;edgeActive:Uint8Array}):Float32Array;
export function preserveOwnerCamera<P,Q,T>(
  camera:{position:{clone():P;copy(value:P):unknown};quaternion:{clone():Q;copy(value:Q):unknown};zoom:number;near:number;far:number;updateProjectionMatrix():void;updateMatrixWorld():void},
  controls:{target:{clone():T;copy(value:T):unknown};minDistance:number;maxDistance:number}):()=>void;
