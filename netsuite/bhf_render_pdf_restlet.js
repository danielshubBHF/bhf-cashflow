/**
 * BHF - PDF of a transaction (PO, bill, invoice) for the cashflow sync.
 * GET ?id=<internal id>              ->  { name, base64, source: "netsuite" }   NetSuite's printout
 * GET ?id=<internal id>&attached=1   ->  { name, base64, source: "attached" }   the PDF attached to the record
 *                                         (e.g. the supplier's own invoice on a bill), else the printout
 *
 * @NApiVersion 2.1
 * @NScriptType Restlet
 */
define(['N/render', 'N/search', 'N/file'], (render, search, file) => {
  const attachedPdf = (id) => {
    let found = null;
    search.create({
      type: search.Type.TRANSACTION,
      filters: [['internalid', 'anyof', id], 'and', ['mainline', 'is', 'T']],
      columns: [
        search.createColumn({ name: 'internalid', join: 'file', sort: search.Sort.DESC }),
        search.createColumn({ name: 'name', join: 'file' }),
      ],
    }).run().each((r) => {
      const fid = r.getValue({ name: 'internalid', join: 'file' });
      const fname = r.getValue({ name: 'name', join: 'file' }) || '';
      if (fid && /\.pdf$/i.test(fname)) {
        found = { fid, fname };
        return false;                        // newest attached PDF
      }
      return true;
    });
    if (!found) return null;
    return { name: found.fname, base64: file.load({ id: found.fid }).getContents(), source: 'attached' };
  };

  const get = (params) => {
    const id = parseInt(params.id, 10);
    if (!id) throw new Error('id required');
    if (params.attached === '1') {
      const a = attachedPdf(id);
      if (a) return a;
    }
    const t = search.lookupFields({ type: search.Type.TRANSACTION, id, columns: ['tranid', 'type'] });
    const pdf = render.transaction({ entityId: id, printMode: render.PrintMode.PDF });
    const ref = (t.tranid || String(id)).replace(/[^\w.-]+/g, '_');
    const type = (t.type && t.type[0] && t.type[0].text) || 'Transaction';
    return { name: `${type.replace(/\s+/g, '_')}_${ref}.pdf`, base64: pdf.getContents(), source: 'netsuite' };
  };
  return { get };
});
