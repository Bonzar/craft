export type OwnerLiveHandle = { close(): void };
export function openOwnerLive(args:{
  allowedOrigin:string;
  csrf:string;
  initialEpoch?:string|null;
  onInvalidate?:(retiredEpoch:string|null)=>void;
  onState:(state:'connecting'|'live'|'disconnected')=>void;
  onRefresh:(reason:'changed'|'reset')=>void|Promise<void>;
}):OwnerLiveHandle;
