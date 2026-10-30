// glitch::video::CFPSCounter::CFPSCounter() — Premium ELF VA 0x0077bb7c (56 B, A32).
// Mangled: _ZN6glitch5video11CFPSCounterC2Ev (base-object ctor).
// C1 at 0x0077bbb4 is byte-identical (no virtual inheritance).
// See fps_counter.h for field layout and evidence.
#include <new>
#include "fps_counter.h"

using nova3::recovered::fps_counter::CFPSCounter;

extern "C" CFPSCounter* nova3_recovered_fps_counter_ctor(CFPSCounter* self) {
    // Placement-new invokes the value-initialising constructor.
    return new (self) CFPSCounter();
}