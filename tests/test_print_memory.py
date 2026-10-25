import unittest

from PIL import Image

from print_manager import resize_for_print


class PrintMemoryTests(unittest.TestCase):
    def test_downsamples_to_physical_print_size_at_render_dpi(self):
        image = Image.new("RGB", (1200, 3600), "white")

        prepared = resize_for_print(
            image,
            box=(0, 0, 1600, 4800),
            dpi_x=1000,
            dpi_y=1000,
            render_dpi=300,
        )

        self.assertEqual(prepared.size, (480, 1440))

    def test_does_not_upscale_an_image_that_fits_render_size(self):
        image = Image.new("RGB", (240, 720), "white")

        prepared = resize_for_print(
            image,
            box=(0, 0, 1600, 4800),
            dpi_x=1000,
            dpi_y=1000,
            render_dpi=300,
        )

        self.assertIs(prepared, image)

    def test_rejects_invalid_printer_dpi(self):
        image = Image.new("RGB", (10, 10), "white")

        with self.assertRaisesRegex(ValueError, "lebih besar dari nol"):
            resize_for_print(image, (0, 0, 100, 100), dpi_x=0, dpi_y=300)


if __name__ == "__main__":
    unittest.main()
