# AI-CAD competitive scan

This August 2026 snapshot maps the public AI-CAD and agentic-engineering surfaces most relevant to MakerBench-HWE, while separating vendor-described capabilities from independently reproducible benchmark evidence.

## Method and evidence boundary

The original capture came from product names saved from LinkedIn URLs in late
June and early July 2026. LinkedIn post bodies were not used. This materialized
scan rechecked the named products against public first-party pages on 2026-08-17.
The capability wording below therefore means "the vendor currently documents
this surface," not "MakerBench has independently validated it." Academic
benchmark claims are reserved for the related-work capture in
[`612-phoenix-agentic-sim-prior-art.md`](612-phoenix-agentic-sim-prior-art.md).

## Primary-sourced public surfaces

| Surface | Publicly documented capability | Artifact / control boundary | MakerBench implication |
| --- | --- | --- | --- |
| [Zoo Design Studio](https://docs.zoo.dev/docs/faq) | Natural-language generation producing editable parametric KCL; the cited FAQ says the Agent API's CAD-generation results "can include editable KCL project files" and that "producing formats such as STEP requires a separate modeling and export step" — STEP appears there as a project export, not as generation output. (Zoo's separate Text-to-CAD API is documented elsewhere as emitting STEP; that surface was not rechecked in this pass and is deliberately not claimed here.) Zoo also documents point-and-click, code-CAD, API, and agent-tool workflows. | KCL is text and versionable; STEP is interoperable B-rep. Zoo says the geometry engine is proprietary while Design Studio is open source. | A strong code-CAD/API baseline. Evaluate executable geometry, editability, manufacturing constraints, and iteration—not whether a plausible render exists. |
| [Adam Copilot](https://adam.new/copilot) / [CADAM](https://adam.new/opensource) | Prompt-driven edits, selection context, feature-tree cleanup, and parametrization inside Onshape and Autodesk Fusion; CADAM is Adam's open-source prompt-to-3D workspace. | Copilot operates on an existing CAD model and feature tree; CADAM is a separate text-to-CAD surface. | Keep greenfield generation and edit-in-context tasks distinct. A model that generates a shape has not necessarily preserved design intent through an edit. |
| [ty\e](https://tylellm.com/) | Browser-native chat, voice, vision, parametric B-rep CAD, and built-in engineering analyses. The product page identifies OCCT, editable features, STEP/DXF handoff, and standards-aware reports. | Vendor-described integrated workflow; no public deterministic evaluation was found in this review. | Closest public voice-to-CAD workflow analog in this capture. Candidate tests belong in a gated task proposal, not in the landscape document itself. See the focused teardown. |
| [SOLIDWORKS AI Virtual Companions](https://www.solidworks.com/product/solidworks-design/ai-virtual-companions) | LEO is documented for prompted assembly structures, STEP-to-parametric features, simulation studies, assembly evaluation, drawings, and design-error assistance. | Embedded in a commercial CAD environment; the current page describes availability and future expansion, but no public reproducible scorecard. | Incumbent reference for in-context CAD control. Compare output artifacts and validation receipts rather than marketing feature counts. |
| [PTC Creo AI Assistant](https://www.ptc.com/en/news/2026/ptc-brings-ai-powered-guidance-to-the-design-environment-with-creo-13) | Creo 13 ships documentation-grounded guidance; model-reading, context-aware validation is described as beta. [PTC's help](https://support.ptc.com/help/creo/creo_ai/usascii/ai_assistant/overview.html) distinguishes the generally available Advise surface from Assist (Beta), which PTC's Creo AI documentation describes as requiring an Advanced AI license. Availability beyond that license requirement was not established in this pass. | Capability maturity differs by tier. Guidance, model interrogation, and geometry modification must not be collapsed into one claim. | Benchmark reports should disclose the exact assistant tier and whether the agent could inspect or mutate the model. |
| [SimScale Engineering AI](https://www.simscale.com/product/engineering-ai/) | Intent-driven simulation setup, execution, evaluation, and governed workflows, including solver/mesh/boundary-condition selection and multi-agent integration. | Vendor product claims; configurations and simulation steps are described as traceable and reproducible, but no MakerBench run is implied. | A reference loop for CAD-to-validation tasks: preserve the setup, solver receipt, checks, and result—not only the prose conclusion. |
| [NVIDIA OpenUSD / Omniverse](https://developer.nvidia.com/openusd) | OpenUSD is the composition framework underlying Omniverse libraries for interoperable 3D worlds, digital twins, and simulation-capable applications. | Interchange and scene composition, not a CAD feature-tree format or proof of manufacturability. | Useful downstream scene/simulation target, but an OpenUSD export cannot substitute for a native CAD or fabrication artifact gate. |

## What this changes for MakerBench

The competitive boundary is no longer "can an AI make 3D geometry?" Public
systems now advertise prompt-to-parametric CAD, contextual editing, voice and
vision input, solver orchestration, and incumbent-CAD integration. MakerBench's
defensible lane is the evidence layer across those workflows:

1. execute the artifact in the disclosed toolchain;
2. measure geometric and manufacturing requirements deterministically;
3. preserve editability, provenance, and validation receipts;
4. separate agent/runtime failures from model quality; and
5. compare blind generation with feedback-enabled iteration.

This also argues against one undifferentiated "AI CAD" score. Greenfield
generation, edit-in-context, CAD-to-simulation, and fabrication-packet handoff
have different tool privileges and failure modes.

## Leads not promoted to verified rows

The capture also named Leo AI, DraftAid, Ragnar, CADGPT, Aurorin CAD, Limitless
Labs, MecAgent, Théia / Spare Parts 3D, STL2CAD, and
an ambiguous "AICAD" label. They are intentionally absent from the verified
table: this pass did not establish a sufficiently specific first-party source
for the exact captured claim. A future landscape sweep may add a lead after it
records a stable primary source and distinguishes shipped capability from a
roadmap or social-media description.

## Maintenance note

Product capabilities and availability are volatile. Recheck every linked page
at the next quarterly landscape sweep and date any material status change.
Parent capture: [#610](https://github.com/tonykoop/makerbench-hwe/issues/610).
