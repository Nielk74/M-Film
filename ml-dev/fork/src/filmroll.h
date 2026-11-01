#ifndef _filmroll_h_
#define _filmroll_h_

struct event;

/* button hook, called from handle_common_events_by_feature; 0 = handled */
int handle_filmroll_keys(struct event *event);

/* called instead of opening the Magic Lantern menu (menu.c) */
void filmroll_menu_requested();

/* ML's own LiveView overlays stay out of the way of the film OS and the
 * viewfinder HUD (always, in this fork) */
int filmroll_overlays_active();

#if defined(FEATURE_FILMROLL) && defined(FEATURE_VRAM_RGBA)
#include <stdint.h>
/* Non-NULL only while the film OS owns the indexed canvas. The normal
 * ML palette remains in use for the HUD, messages, and Canon menus. */
extern const uint32_t * volatile filmroll_rgba_palette;
#endif

#endif
