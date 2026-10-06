/**
 * BHF - render a transaction (PO, bill, invoice) as PDF for the cashflow sync.
 * GET ?id=<internal id>  ->  { name, base64 }
 *
 * @NApiVersion 2.1
 * @NScriptType Restlet
 */
define(['N/render', 'N/search'], (render, search) => {
  const get = (params) => {
    const id = parseInt(params.id, 10);
    if (!id) throw new Error('id required');
    const t = search.lookupFields({ type: search.Type.TRANSACTION, id, columns: ['tranid', 'type'] });
    const pdf = render.transaction({ entityId: id, printMode: render.PrintMode.PDF });
    const ref = (t.tranid || String(id)).replace(/[^\w.-]+/g, '_');
    const type = (t.type && t.type[0] && t.type[0].text) || 'Transaction';
    return { name: `${type.replace(/\s+/g, '_')}_${ref}.pdf`, base64: pdf.getContents() };
  };
  return { get };
});
