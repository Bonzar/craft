export type OwnerLiveHandle = { close(): void };
export function openOwnerLive(args:{
  allowedOrigin:string;
  csrf:string;
  onState:(state:'connecting'|'live'|'disconnected')=>void;
  onRefresh:(reason:'changed'|'reset')=>void|Promise<void>;
}):OwnerLiveHandle;
