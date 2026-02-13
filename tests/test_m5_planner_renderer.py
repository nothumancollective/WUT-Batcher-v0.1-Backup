from __future__ import annotations



import unittest



from app.batch_planner import expand_versions

from app.cfg_renderer import render_cfg_text

from app.models import Batch, ParamSelection, SweepSpec





class PlannerRendererTests(unittest.TestCase):

    def test_single_expansion_is_deterministic(self) -> None:

        batch = Batch(

            batch_id="B001",

            project_id="P001",

            selected_params={"Throat.Diameter": ParamSelection(value=25.0)},

            sweeps={

                "Length": SweepSpec(start=80, end=100, steps=3),

                "Coverage.Angle": SweepSpec(start=40, end=50, steps=2),

            },

            sweep_mode="single",

        )

        versions = expand_versions(batch, {"fixed_params": {}, "limits": {}})

        self.assertEqual([v.version_id for v in versions], ["V001", "V002", "V003", "V004", "V005"])

        # Alphabetic key order: Coverage.Angle before Length.

        self.assertEqual(float(versions[0].parameters["Coverage.Angle"]), 40.0)

        self.assertEqual(float(versions[2].parameters["Length"]), 80.0)



    def test_combined_expansion_is_cartesian_and_stable(self) -> None:

        batch = Batch(

            batch_id="B001",

            project_id="P001",

            sweeps={

                "Length": SweepSpec(start=80, end=90, steps=2),

                "Coverage.Angle": SweepSpec(start=40, end=50, steps=2),

            },

            sweep_mode="combined",

        )

        versions = expand_versions(batch, {"fixed_params": {}, "limits": {}})

        self.assertEqual(len(versions), 4)

        self.assertEqual(versions[0].version_id, "V001")

        self.assertEqual(float(versions[0].parameters["Coverage.Angle"]), 40.0)

        self.assertEqual(float(versions[0].parameters["Length"]), 80.0)



    def test_cfg_renderer_enforces_mandatory_source_block(self) -> None:

        template = """ABEC.AkabakMode = 0\nLE = foo\nLE.Voltage = 2.5\nLength = 70\nSource.Shape = 1\n"""

        cfg = render_cfg_text(

            template_text=template,

            parameters={

                "Length": 90,

                "ABEC.AkabakMode": 9,

                "LE": "bar",

                "LE.Voltage": 99,

                "Source.Shape": 2,

            },

            version_id="V001",

        )

        self.assertIn("ABEC.AkabakMode    = 1", cfg)

        self.assertIn("LE          = generic25", cfg)

        self.assertIn("LE.Voltage  = 1.0", cfg)

        self.assertIn("Length           = 90", cfg)

        self.assertNotIn("Source.Shape", cfg)





if __name__ == "__main__":

    unittest.main()



