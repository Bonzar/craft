import type {GraphData} from './binary.ts';
import type {Selector} from './logic.ts';
import type {Manifest,Topics} from './contracts.ts';
export interface OwnerGraphStamp{epoch:string;revision:string;cursor:string}
export interface OwnerGraphBaseline extends OwnerGraphStamp{manifest:unknown;topics:unknown;ids:string[];nodes:ArrayBuffer;edges:ArrayBuffer}
export interface OwnerGraphStored extends OwnerGraphStamp{ids:(string|null)[];graph:GraphData;visualPositions:Float32Array;view?:{manifest:Manifest;topics:Topics}|null}
export interface OwnerGraphEvent extends OwnerGraphStamp{[key:string]:unknown}
export interface OwnerGraphCache{take(scope:string|null):Promise<OwnerGraphStored|null>;save(scope:string|null,input:OwnerGraphStored):Promise<boolean>;clear():Promise<void>}
export interface OwnerGraphSession{initial:Promise<void>;close():void}
export function openOwnerGraphSession(options:{selector:Selector;scope?:string|null;verifiedEpoch?:string|null;cache?:OwnerGraphCache;
  onCached:(value:OwnerGraphStored,signal:AbortSignal)=>Promise<OwnerGraphStored>;
  onSnapshot:(value:OwnerGraphBaseline,signal:AbortSignal)=>Promise<OwnerGraphStored>;
  onDelta:(value:OwnerGraphEvent,signal:AbortSignal)=>Promise<OwnerGraphStored>;
  onReset:(value:{epoch:string,reason:string},signal:AbortSignal)=>void|Promise<void>;
  onState?:(state:'connecting'|'snapshot-connecting'|'snapshot-offline'|'offline'|'live'|'unauthorized')=>void;}):OwnerGraphSession;
