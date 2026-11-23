"""Checked reader for NYPD's weekly report PDFs, for weeks whose spreadsheet is lost.

The method is BetaNYC/New-York-City-Budget's PDF-RECIPE.md: the PDF's text layer read
through the rules the PDF draws (the vector grid), a second reading from the rendered page
image (the image grid), an ink-coverage check for anything printed but not read, and every
total NYPD prints checked. No OCR engine is used anywhere: the numbers come from the PDF's
own text layer, and the page image only checks them. A row that fails any check is flagged
for a person and kept as printed; nothing is corrected. See PDF-READER.md.

Needs the optional ``pdf`` extra (pdfplumber, pypdfium2, opencv-python-headless, numpy).
"""
