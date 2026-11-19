"""Bounded aggregate live timings. No source identifiers, text or credentials."""
import time


def wall_ms():
    return round(time.time()*1000,3)


def mark(phases,name,started):
    current=time.monotonic()
    phases[name]=round((current-started)*1000,3)
    return current


def emitted(profile,cursor):
    profile['cursor']=str(cursor);profile['emitted_at_ms']=wall_ms()
    profile['detection_to_emit_ms']=round(profile['emitted_at_ms']-profile['detected_at_ms'],3)


def first_sse_yield(profiles,cursor):
    for profile in reversed(profiles):
        if profile.get('cursor')==str(cursor) and 'first_sse_yield_at_ms' not in profile:
            profile['first_sse_yield_at_ms']=wall_ms()
            profile['emit_to_first_sse_yield_ms']=round(profile['first_sse_yield_at_ms']-profile['emitted_at_ms'],3)
            break
