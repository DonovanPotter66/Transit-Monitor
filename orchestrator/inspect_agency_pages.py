import asyncio
from playwright.async_api import async_playwright
URLS=[('WMATA','https://supplier.wmata.com/psc/supplier_1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL?FolderPath=PORTAL_ROOT_OBJECT.EP_AUC_RESP_INQ_AUC&IgnoreParamTempl=FolderPath%2CIsFolder&IsFolder=false&NoCrumbs=yes'),('MTA','https://www.mta.info/agency/construction-and-development/contracting/current-opportunities'),('MARTA','https://martabid.marta.net/AnticipatedProcurement.aspx'),('DART2','https://www.dart.org/about/doing-business/procurement'),('LA','https://business.metro.net/webcenter/portal/VendorPortal/pages_home/solicitations/openSolicitations')]
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True)
  for n,u in URLS:
   page=await b.new_page()
   try:
    r=await page.goto(u,wait_until='domcontentloaded',timeout=90000); await page.wait_for_timeout(7000); print('\n###',n,r.status if r else None,await page.title()); print((await page.locator('body').inner_text())[:1200].replace('\n',' | ')); print('tables',await page.locator('table').count(),'rows',await page.locator('tr').count())
   except Exception as e: print('\n###',n,'ERROR',repr(e))
   await page.close()
  await b.close()
asyncio.run(main())
