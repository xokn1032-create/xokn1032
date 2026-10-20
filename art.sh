echo "Downloading j9qb01011_drz.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=J9QB01011%2Fj9qb01011_drz.fits" --output "MAST_2026-10-20T20_09_22.998Z/HST/j9qb01011_drz.fits" --fail --create-dirs

echo "Downloading j96701011_drz.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=J96701011%2Fj96701011_drz.fits" --output "MAST_2026-10-20T20_13_52.371Z/HST/j96701011_drz.fits" --fail --create-dirs

echo "Downloading jfeu01010_drc.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=JFEU01010%2Fjfeu01010_drc.fits" --output "MAST_2026-10-20T20_16_07.427Z/HST/jfeu01010_drc.fits" --fail --create-dirs
