export type OwnerLiveHandle = { close(): void };
export function openOwnerLive(args:{
  allowedOrigin:string;
  transport?:'direct'|'ordinary';
  csrf:string;
  initialEpoch?:string|null;
  onStartupFailure?:(()=>void|Promise<void>)|null;
  onBootstrap?:(epoch:string)=>boolean;
  onInvalidate?:(retiredEpoch:string|null)=>void;
  onState:(state:'connecting'|'registered'|'live'|'disconnected')=>void;
  onRefresh:(reason:'changed'|'reset',epoch:string|null)=>void|Promise<void>;
}):OwnerLiveHandle;

export type FirstOwnerSnapshot={ready:Promise<void>; accept(epoch:string):boolean; fallback():boolean; close():void};
export function firstOwnerSnapshot(load:(epoch:string|null)=>void|Promise<void>):FirstOwnerSnapshot;
