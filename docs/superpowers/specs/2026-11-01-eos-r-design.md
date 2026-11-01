# EOS R experimental port

The user wants a fork of M-Film and a practical attempt to run it on the original
Canon EOS R. Preserve the film interface and existing EOS M behavior. This first
increment establishes an independently buildable DIGIC 8 port; physical camera
acceptance and complete CR3 film development are not established by host tests.

Use the current upstream EOS R core at commit
142472e95054d73640f50789ac0fd66325499d82, while preserving the original entry point
and revision for other bodies. Add a separate experimental build and CI job.
Require explicit opt-in and no CR2 darkroom when patching R.180. Preserve the
upstream property whitelist. ISO/WB adjustments are manual. Use the upstream
Picture Style API for the existing preview recipe; never describe it as the full
film look. Retain the camera support table's unsupported status until tested.

The display adapter builds a 256-entry RGBA lookup from the existing indexed
canvas and calibrated screen conversion. ML's conversion reads that table only
while the film interface owns the canvas. Normal overlays keep ML's palette.
Do not touch the EOS R's NULL legacy LCD palette or write indexed bytes into a
32-bit surface. Log CR3 names and use LiveView-only behavior. No installer,
firmware download, flash, property-whitelist expansion or hardware register
guessing is part of this increment. Default to manual UI launch and HUD off.

Verify the actual generated display dispatch, transparency, colour indices above
79, fades, palette restoration, atomic patcher rejection and idempotence. Run all
existing simulator scenes and compare both decoder modes to synthetic known RAW
samples. Cross-compile stock EOS R, the port, and the original EOS M regression.
Record compiler, source, footprint, limitations and all unperformed hardware tests.
