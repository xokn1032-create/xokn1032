echo "Downloading j9fw40010_drz.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=J9FW40010%2Fj9fw40010_drz.fits" --output "MAST_2026-10-20T21_49_15.224Z/HST/j9fw40010_drz.fits" --fail --create-dirs

echo "Downloading jabu01010_drc.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=JABU01010%2Fjabu01010_drc.fits" --output "MAST_2026-10-20T21_47_43.904Z/HST/jabu01010_drc.fits" --fail --create-dirs

echo "Downloading j8ms04uqq_drz.fits..."
curl -L -X GET "https://mast.stsci.edu/search/hst/api/v0.1/retrieve_product?product_name=J8MS04UQQ%2Fj8ms04uqq_drz.fits" --output "MAST_2026-10-20T21_46_04.361Z/HST/j8ms04uqq_drz.fits" --fail --create-dirs
