import {Vector3} from 'three';
import type {PerspectiveCamera} from 'three';
/** Change orbit depth along the optical axis, never camera pose or Source coordinates. */
export function reanchorLocalView(camera:PerspectiveCamera,target:Vector3,landmark:ArrayLike<number>,minimum:number,maximum:number):boolean{const forward=camera.getWorldDirection(new Vector3()),depth=new Vector3(landmark[0]!,landmark[1]!,landmark[2]!).sub(camera.position).dot(forward);if(!Number.isFinite(depth)||depth<=0)return false;const distance=Math.max(minimum,Math.min(maximum,depth));if(!Number.isFinite(distance)||distance<=0)return false;target.copy(camera.position).addScaledVector(forward,distance);return true;}
