/* Research comparison only: resolve external dependencies to their observed
 * addresses, never to original code. The output cannot implement billing. */
ENTRY(InAppBilling_ClearItemList)
_ZN12InAppBilling13clearItemListEv = 0x000d4d58;
_ZN12InAppBilling8getStateEv = 0x000d5148;
SECTIONS {
  .text.InAppBilling_ClearItemList 0x000d4dbc : {
    KEEP(*(.text.InAppBilling_ClearItemList))
  }
  .text.InAppBilling_GetState 0x000d5224 : {
    KEEP(*(.text.InAppBilling_GetState))
  }
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
