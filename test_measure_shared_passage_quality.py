"""Regression checks for independently measured selection evidence."""
import unittest
from scripts.measure_shared_passage_quality import fraction, pdf_box, coverage


class SourceMeasurementTests(unittest.TestCase):
    def test_margin_crop_is_subtracted_after_pdf_dpi_scaling(self):
        page={'metadata':{'dpi':144,'margin_crop_box':{'left':100,'top':50}}}
        self.assertEqual(pdf_box([60,30,80,70],page),(20,10,60,90))

    def test_empty_denominator_is_incomplete(self):
        self.assertIsNone(fraction([]))
        self.assertEqual(fraction([('lost-glyph',False)]),{
            'numerator':0,'denominator':1,'failed_ids':['lost-glyph']})

    def test_intersection_cannot_hide_partial_line_loss(self):
        self.assertEqual(coverage((0,0,100,10),(0,0,50,10)),.5)
        self.assertEqual(coverage((0,0,100,10),(0,20,100,30)),0)
        self.assertEqual(coverage((0,0,100,10),(-5,-5,105,15)),1)


class MatchingAdversarialTests(unittest.TestCase):
    def test_partial_region_and_duplicate_predictions_are_false_positives(self):
        from scripts.measure_shared_passage_quality import assign_detections
        units=[{'id':'partial','range':[1,3],'source_region_iou':.79},
               {'id':'correct','range':[1,3],'source_region_iou':.95},
               {'id':'duplicate','range':[1,3],'source_region_iou':.9}]
        results,selected=assign_detections(units,{(1,3)})
        self.assertEqual(results,{'partial':False,'correct':True,'duplicate':False})
        self.assertEqual(selected,{(1,3):'correct'})

    def test_union_region_iou_counts_gaps_and_extra_regions(self):
        from scripts.measure_shared_passage_quality import region_iou
        expected=[(0,0,10,10),(0,20,10,30)]
        self.assertAlmostEqual(region_iou(expected,[(0,0,10,30)]),2/3)
        self.assertEqual(region_iou(expected,expected),1)

    def test_white_opaque_figure_fill_does_not_hide_lost_or_extra_glyphs(self):
        try:
            import numpy as np
            import cv2
        except ImportError:
            self.skipTest('locked release venv supplies numpy/cv2')
        from scripts.measure_shared_passage_quality import final_foreground
        rgba=np.full((50,50,4),255,np.uint8)
        rgba[10:15,10:15,:3]=0
        visible,surfaces=final_foreground(rgba)
        self.assertEqual(len(surfaces),1)
        self.assertEqual(int(visible.sum()),25)
        self.assertFalse(visible[25,25]) # erased required black glyph has no ink
        rgba[30:35,30:35,:3]=0
        with_intrusion,_=final_foreground(rgba)
        self.assertEqual(int(with_intrusion.sum()),50) # extra graphic ink stays measurable
        frame=np.zeros((100,100,4),np.uint8);frame[:,20:23,:]=255
        visible,_=final_foreground(frame)
        self.assertEqual(int(visible.sum()),300) # white inverted rule is retained
        shaded=np.full((50,50,4),248,np.uint8);shaded[...,3]=32;shaded[10:15,10:15,3]=255
        visible,surfaces=final_foreground(shaded)
        self.assertEqual(surfaces,[]) # translucent grey S2 cell is not an opaque image
        self.assertTrue(visible[12,12]) # white inverted glyph must remain visible


class ObserverFlowTests(unittest.TestCase):
    def test_reversed_registered_fragment_positions_fail(self):
        from scripts.measure_shared_passage_quality import reading_order_valid
        self.assertTrue(reading_order_valid([('f1',30),('f2',120)]))
        self.assertFalse(reading_order_valid([('f1',120),('f2',30)]))

    def test_actual_observer_binds_source_and_no_export_is_measured_failure(self):
        import json
        import hashlib
        import tempfile
        from pathlib import Path
        import fitz
        from scripts.measure_shared_passage_quality import measure_shared
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.pdf';doc=fitz.open();page=doc.new_page(width=200,height=200);page.insert_text((20,40),'Test required source');doc.save(source);doc.close()
            sha=hashlib.sha256(source.read_bytes()).hexdigest()
            fragment={'id':'f1','page':1,'reading_order':1,'box':[15,20,180,60],
                      'source_lines':[{'text':'Test required source','box':[20,28,130,43]}],
                      'first_content_line_box':[20,28,130,43]}
            truth={'documents':[{'id':'source','source':{'path':str(source),'sha256':sha},'groups':[{'id':'g1','range':[1,2],'fragments':[fragment]}]}]}
            (root/'gt.json').write_text(json.dumps(truth));(root/'session.json').write_text(json.dumps({'input_files':[str(source)],'problems':[]}))
            (root/'pages.json').write_text(json.dumps([{'page_id':'source-page-001','metadata':{'dpi':72}}]))
            report=measure_shared(root/'gt.json','source',root/'session.json',root/'pages.json',root/'out')
            self.assertEqual(report['metrics']['detection']['recall']['numerator'],0)
            self.assertEqual(report['metrics']['detection']['recall']['denominator'],1)
            self.assertIsNone(report['metrics']['detection']['precision'])
            self.assertIn('no-missing-instance',report['hard_failures'])
            self.assertFalse(next(a['passed'] for a in report['assertions'] if a['id']=='edb-present'))




class PixelAdversarialFlowTests(unittest.TestCase):
    def test_erased_two_pixel_required_minus_is_measured_loss(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('locked release venv supplies cv2')
        import tempfile
        from pathlib import Path
        from PIL import Image,ImageDraw
        import fitz
        from scripts.measure_shared_passage_quality import measure_final_pixels
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'tiny.pdf';doc=fitz.open();page=doc.new_page(width=300,height=200)
            for y,text in [(35,'Required first line anchor'),(70,'Required middle line anchor'),(105,'Required last line anchor')]:page.insert_text((20,y),text,fontsize=10)
            page.draw_rect(fitz.Rect(250,140,252,141),color=None,fill=(0,0,0),width=0)
            source_lines=[]
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):source_lines.append({'text':''.join(s['text'] for s in line['spans']),'box':list(line['bbox'])})
            pix=page.get_pixmap(alpha=False);raw=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);raw.save(root/'raw.png');doc.save(source);doc.close()
            final=raw.convert('RGBA');ImageDraw.Draw(final).rectangle((248,138,255,144),fill='white');final.save(root/'final.png')
            fragment={'id':'f1','page':1,'reading_order':1,'box':[10,10,290,180],'source_lines':source_lines,'first_content_line_box':source_lines[0]['box']}
            gt={'groups':[{'id':'g1','range':[1,2],'fragments':[fragment]}]}
            problems=[{'id':'p1','passageRange':{'start':1,'end':2},'imagePath':str(root/'raw.png')}]
            metrics,_,details=measure_final_pixels(gt,problems,{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'final.png'},root,{'p1'})
            self.assertEqual(details['incomplete'],[])
            self.assertLess(metrics['recall']['numerator'],metrics['recall']['denominator'])
            self.assertTrue(metrics['recall']['failed_ids'])




class InternalLineOrderTests(unittest.TestCase):
    def test_final_line_swap_inside_one_fragment_fails_order_and_pixels(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('locked release venv supplies cv2')
        import tempfile
        from pathlib import Path
        from PIL import Image
        import fitz
        from scripts.measure_shared_passage_quality import measure_final_pixels,content_joining_metrics
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'order.pdf';doc=fitz.open();page=doc.new_page(width=300,height=200)
            for y,text in [(35,'AAAAA MMMMM NNNNN'),(70,'WWWWW OOOOO HHHHH'),(105,'ZZZZZ EEEEE SSSSS')]:page.insert_text((20,y),text,fontsize=10)
            lines=[]
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):lines.append({'text':''.join(s['text'] for s in line['spans']),'box':list(line['bbox'])})
            pix=page.get_pixmap(alpha=False);raw=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);raw.save(root/'raw.png');doc.save(source);doc.close()
            final=raw.convert('RGBA');upper=final.crop((0,20,300,45));lower=final.crop((0,55,300,80));final.paste(lower,(0,20));final.paste(upper,(0,55));final.save(root/'final.png')
            fragment={'id':'f1','page':1,'reading_order':1,'box':[10,10,290,180],'source_lines':lines,'first_content_line_box':lines[0]['box']}
            gt={'groups':[{'id':'g1','range':[1,2],'fragments':[fragment]}]}
            metrics,_,details=measure_final_pixels(gt,[{'id':'p1','passageRange':{'start':1,'end':2},'imagePath':str(root/'raw.png')}],{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'final.png'},root,{'p1'})
            joining,order=content_joining_metrics(gt,details['registrations'])
            self.assertEqual(details['incomplete'],[])
            self.assertEqual(order['membership']['numerator'],3) # all identities remain, at wrong positions
            self.assertEqual(joining['edge_recall']['denominator'],2) # single fragment has internal edges
            self.assertLess(joining['edge_recall']['numerator'],2)
            self.assertFalse(order['orders'][0]['passed'])
            self.assertEqual(order['orders'][0]['actual'],['f1:line-2','f1:line-1','f1:line-3'])
            self.assertLess(metrics['recall']['numerator'],metrics['recall']['denominator'])
            # A copied line at a new physical location must fail uniqueness even
            # though every original line and its relative order remain intact.
            duplicate=raw.convert('RGBA');duplicate.paste(upper,(0,125));duplicate.save(root/'duplicate.png')
            _,_,duplicate_details=measure_final_pixels(gt,[{'id':'p1','passageRange':{'start':1,'end':2},'imagePath':str(root/'raw.png')}],{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'duplicate.png'},root,{'p1'})
            duplicate_joining,_=content_joining_metrics(gt,duplicate_details['registrations'])
            self.assertLess(duplicate_joining['unit_uniqueness']['numerator'],duplicate_joining['unit_uniqueness']['denominator'])
            self.assertGreater(duplicate_joining['edge_precision']['denominator'],2)
            self.assertLess(duplicate_joining['edge_precision']['numerator'],duplicate_joining['edge_precision']['denominator'])
            # A better exact copy must not hide a slightly degraded but valid
            # original. Its old location remains within the 1px spatial tolerance.
            import numpy as np
            damaged=np.asarray(raw.convert('RGBA')).copy();yy,xx=np.indices(damaged.shape[:2]);holes=(xx<75)&((xx+yy)%2==0)&(yy>=20)&(yy<45)&np.any(damaged[...,:3]<200,axis=2);damaged[holes,:3]=255
            adversarial=Image.fromarray(damaged);adversarial.paste(upper,(25,125));adversarial.save(root/'damaged-plus-copy.png')
            _,_,adversarial_details=measure_final_pixels(gt,[{'id':'p1','passageRange':{'start':1,'end':2},'imagePath':str(root/'raw.png')}],{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'damaged-plus-copy.png'},root,{'p1'})
            adversarial_joining,_=content_joining_metrics(gt,adversarial_details['registrations'])
            first=adversarial_details['registrations'][0]['line_units'][0]
            self.assertGreaterEqual(len(first['occurrences']),2)
            self.assertTrue(any(.8<=o['ncc']<.98 for o in first['occurrences']))
            self.assertLess(adversarial_joining['unit_uniqueness']['numerator'],adversarial_joining['unit_uniqueness']['denominator'])





class ExplicitGuideMaskTests(unittest.TestCase):
    def test_only_declared_guide_pixels_are_ignored_and_glyphs_win(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('locked release venv supplies numpy')
        from scripts.measure_shared_passage_quality import optional_guide_mask
        gt={'optional_shared_guide_masks':[{'id':'declared','page':1,'box':[10,10,12,20]}]}
        page={'metadata':{'dpi':72}}
        ignored,audit=optional_guide_mask(gt,1,page,(30,30,3),[[10.1,12.1,10.9,12.9]])
        self.assertTrue(ignored[10,10])
        self.assertFalse(ignored[12,10]) # even a tiny required glyph is protected
        self.assertTrue(ignored[12,11])
        self.assertFalse(ignored[:,9].any())
        self.assertFalse(ignored[:,12].any())
        self.assertFalse(ignored[20].any())
        self.assertEqual(audit['requested_pixels'],20)
        self.assertEqual(audit['protected_glyph_pixels_subtracted'],1)
        other,_=optional_guide_mask(gt,2,page,(30,30,3),[])
        self.assertFalse(other.any())
        absent,_=optional_guide_mask({},1,page,(30,30,3),[])
        self.assertFalse(absent.any())




class NativeCutoutRegistrationTests(unittest.TestCase):
    def test_transparent_border_trim_is_translation_not_raw_dimension_scaling(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('locked release venv supplies cv2')
        import tempfile
        from pathlib import Path
        from PIL import Image,ImageDraw
        import fitz
        from scripts.measure_shared_passage_quality import measure_final_pixels
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'trim.pdf';doc=fitz.open();page=doc.new_page(width=300,height=200)
            for y,text in [(35,'Required first source anchor'),(70,'Required middle source anchor'),(105,'Required final source anchor')]:page.insert_text((20,y),text,fontsize=10)
            page.draw_line(fitz.Point(280,15),fitz.Point(280,175),width=.36,color=(0,0,0))
            lines=[]
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):lines.append({'text':''.join(s['text'] for s in line['spans']),'box':list(line['bbox'])})
            pix=page.get_pixmap(alpha=False);raw=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);raw.save(root/'raw.png');doc.save(source);doc.close()
            cutout=raw.convert('RGBA').crop((5,3,295,197));cutout.save(root/'cutout.png')
            fragment={'id':'f1','page':1,'reading_order':1,'box':[10,10,290,180],'source_lines':lines,'first_content_line_box':lines[0]['box']}
            gt={'groups':[{'id':'g1','range':[1,2],'fragments':[fragment]}],'optional_shared_guide_masks':[{'id':'declared-guide','page':1,'box':[279.3,14.3,280.7,175.7]}]}
            problems=[{'id':'p1','passageRange':{'start':1,'end':2},'imagePath':str(root/'raw.png'),'boardRenderPath':str(root/'cutout.png')}]
            metrics,_,details=measure_final_pixels(gt,problems,{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'cutout.png'},root,{'p1'})
            self.assertEqual(details['incomplete'],[])
            row=details['registrations'][0]
            self.assertEqual((row['dx'],row['dy']),(-5,-3))
            self.assertEqual(row['reference_cutout_size'],[290,194])
            self.assertEqual(metrics['recall']['numerator'],metrics['recall']['denominator'])
            self.assertEqual(metrics['precision']['numerator'],metrics['precision']['denominator'])
            # The same explicitly optional source decoration may be absent too.
            removed=cutout.copy();ImageDraw.Draw(removed).rectangle((274,10,276,173),fill='white');removed.save(root/'removed.png')
            absent,_,absent_details=measure_final_pixels(gt,problems,{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'removed.png'},root,{'p1'})
            self.assertEqual(absent_details['incomplete'],[])
            self.assertEqual(absent['recall']['numerator'],absent['recall']['denominator'])
            self.assertEqual(absent['precision']['numerator'],absent['precision']['denominator'])

if __name__=='__main__': unittest.main()
