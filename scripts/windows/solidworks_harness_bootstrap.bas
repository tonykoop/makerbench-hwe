' =============================================================================
' UNVALIDATED — written from API-surface knowledge, not tested against a live
' SolidWorks install. Needs manual verification on Windows before first real
' use. This is VBA SOURCE, not a runnable .swp — see "One-time setup" below.
'
' Part of the #627 SolidWorks CAD-backend axis job-dir runner. Companion to
' scripts/windows/solidworks_job_watcher.ps1, which drives this macro via
' ISldWorks::RunMacro2 once per pending job.
'
' One-time setup (a human does this once, in SolidWorks, before the watcher
' can use it):
'   1. Tools > Macro > New... to create a blank macro; paste this file's
'      code into its "Module1" code pane.
'   2. Tools > Options > VBA project (or Trust Center, depending on version):
'      enable "Trust access to the VBA project object model" — HarnessMain
'      below needs Application.VBE to inject the entrant's code at runtime,
'      same requirement Excel/Word self-modifying macros have.
'   3. Save the macro as solidworks_job_watcher.ps1's -BootstrapMacroPath
'      default: scripts/windows/solidworks_harness_bootstrap.swp
'
' What HarnessMain does, each time RunMacro2 calls it:
'   1. Reads the plain-text handoff file the watcher just wrote
'      (%TEMP%\makerbench_solidworks_handoff\handoff.txt): STL path, PNG
'      path, result-marker path, units-marker path, then the entrant's raw
'      `Sub BuildPart()` VBA source after a "---ENTRANT-VBA---" line.
'   2. Creates a new blank part document and forces MMGS (millimeter-gram-
'      second) units — the #627 "SolidWorks STEP exports are in inches"
'      gotcha only bites when a document is left in IPS; forcing MMGS before
'      building sidesteps needing any downstream unit conversion.
'   3. Compiles the entrant source into a fresh standard module
'      ("EntrantModule") via the VBA Extensibility object model
'      (Application.VBE.ActiveVBProject.VBComponents.Add) and calls its
'      BuildPart via VBA's late-bound `Run` function — this whole step is the
'      part most likely to need adjustment on a real install; if
'      Application.VBE is not reachable here, the fallback is to abandon
'      dynamic injection and instead template a small, fixed set of
'      parametric macros rather than fully arbitrary entrant VBA.
'   4. Exports the built part to STL (forced MMGS) and a PNG preview via
'      SaveAs3, then writes "DONE" to the result marker.
'   5. On any error, writes "ERROR: <message>" to the result marker instead
'      of raising — the watcher script maps that to a jobdir "error" status,
'      never a silent hang.
'
' swSaveAsCurrentVersion / swSaveAsOptions_e / swUnitSystem_e constants below
' are quoted from general recollection of the SolidWorks API and were not
' checked against a specific version's object browser; verify enum values
' before first real use.

Public swApp As SldWorks.SldWorks
Public Part As SldWorks.ModelDoc2

Sub HarnessMain()
    Dim handoffPath As String
    handoffPath = Environ$("TEMP") & "\makerbench_solidworks_handoff\handoff.txt"

    Dim stlOut As String, pngOut As String, resultPath As String, unitsPath As String
    Dim entrantSource As String
    If Not ReadHandoff(handoffPath, stlOut, pngOut, resultPath, unitsPath, entrantSource) Then
        WriteResult Environ$("TEMP") & "\makerbench_solidworks_handoff\result.txt", "ERROR: could not read handoff file"
        Exit Sub
    End If

    On Error GoTo HandlerFailed

    Set swApp = Application.SldWorks
    swApp.Visible = True

    ' New blank part from the default part template.
    Dim templatePath As String
    templatePath = swApp.GetUserPreferenceStringValue(swDefaultTemplatePart) ' swUserPreferenceStringValue_e.swDefaultTemplatePart
    Set Part = swApp.NewDocument(templatePath, 0, 0, 0)
    If Part Is Nothing Then
        WriteResult resultPath, "ERROR: NewDocument returned Nothing (check default part template path)"
        Exit Sub
    End If

    ' Force MMGS so the STL export (and the entrant's own dimensioning) is
    ' unambiguously millimeters — sidesteps the inches-STEP-export gotcha
    ' entirely rather than converting after the fact.
    Part.Extension.SetUserPreferenceInteger swUnitSystem, 0, swUNITSYSTEM_MMGS
    WriteResult unitsPath, "mm"

    ' Dynamically compile the entrant's BuildPart into a fresh module and run
    ' it. Requires "Trust access to the VBA project object model" (see
    ' header). If this throws, the On Error handler below reports it clearly
    ' rather than leaving the job stuck.
    Dim vbProj As Object, newModule As Object
    Set vbProj = Application.VBE.ActiveVBProject
    Set newModule = vbProj.VBComponents.Add(1) ' vbext_ct_StdModule
    newModule.Name = "EntrantModule"
    newModule.CodeModule.AddFromString entrantSource

    Run "EntrantModule.BuildPart"

    Part.ViewZoomtofit2

    ' STL export.
    Dim exportData As Object
    Set exportData = swApp.GetExportFileData(1) ' swExportDataFileType_e.swExportStlData
    Dim errs As Long, warns As Long
    Dim saveOk As Boolean
    saveOk = Part.Extension.SaveAs3(stlOut, 0, 0, exportData, "", errs, warns) ' 0 = swSaveAsCurrentVersion
    If Not saveOk Then
        WriteResult resultPath, "ERROR: STL SaveAs3 failed (errs=" & errs & ", warns=" & warns & ")"
        Exit Sub
    End If

    ' Preview PNG: Save As to a raster format is a documented SolidWorks
    ' Save-As target from the GUI; the exact ExportData object (if any) a
    ' .png SaveAs3 call needs was not verified — this may need a plain
    ' SaveAs2/SaveAs3 call with a Nothing ExportData, or a dedicated
    ' `Part.Extension.SaveAs` overload instead.
    On Error Resume Next
    Part.Extension.SaveAs3 pngOut, 0, 0, Nothing, "", errs, warns
    On Error GoTo HandlerFailed

    WriteResult resultPath, "DONE"
    Exit Sub

HandlerFailed:
    WriteResult resultPath, "ERROR: " & Err.Description
End Sub

Private Function ReadHandoff(ByVal path As String, ByRef stlOut As String, ByRef pngOut As String, _
                              ByRef resultPath As String, ByRef unitsPath As String, ByRef entrantSource As String) As Boolean
    On Error GoTo Failed
    Dim fnum As Integer
    fnum = FreeFile
    Open path For Input As #fnum

    Line Input #fnum, stlOut
    Line Input #fnum, pngOut
    Line Input #fnum, resultPath
    Line Input #fnum, unitsPath

    Dim marker As String
    Line Input #fnum, marker ' "---ENTRANT-VBA---"

    Dim buf As String, line As String
    Do While Not EOF(fnum)
        Line Input #fnum, line
        buf = buf & line & vbCrLf
    Loop
    Close #fnum

    entrantSource = buf
    ReadHandoff = True
    Exit Function

Failed:
    ReadHandoff = False
End Function

Private Sub WriteResult(ByVal path As String, ByVal message As String)
    On Error Resume Next
    Dim fnum As Integer
    fnum = FreeFile
    Open path For Output As #fnum
    Print #fnum, message
    Close #fnum
End Sub
