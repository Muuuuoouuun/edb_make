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
            # Three actual-final anchors disagree after a swap. This is an
            # explicit unproven mapping, never a perfect preservation claim.
            self.assertEqual(details['incomplete'],['f1:registration-unproven'])
            self.assertIsNone(metrics['recall'])
            self.assertFalse(details['registrations'][0]['registration_valid'])
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
            # V1 publishes the native raw grid even when the preview is trimmed.
            # Register the actual native primary, preserving every source pixel.
            raw.convert('RGBA').save(root/'native.png')
            native,_,native_details=measure_final_pixels(gt,problems,{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'native.png'},root,{'p1'})
            self.assertEqual(native_details['incomplete'],[])
            self.assertEqual((native_details['registrations'][0]['dx'],native_details['registrations'][0]['dy']),(0,0))
            self.assertEqual(native_details['registrations'][0]['reference_cutout_size'],[300,200])
            self.assertEqual(native['recall']['numerator'],native['recall']['denominator'])
            # The same explicitly optional source decoration may be absent too.
            removed=cutout.copy();ImageDraw.Draw(removed).rectangle((274,10,276,173),fill='white');removed.save(root/'removed.png')
            absent,_,absent_details=measure_final_pixels(gt,problems,{'source-page-001':{'metadata':{'dpi':72}}},source,{'p1':root/'removed.png'},root,{'p1'})
            self.assertEqual(absent_details['incomplete'],[])
            self.assertEqual(absent['recall']['numerator'],absent['recall']['denominator'])
            self.assertEqual(absent['precision']['numerator'],absent['precision']['denominator'])

class GrayFigureGlyphIdentityTests(unittest.TestCase):
    def fixture(self):
        import cv2,numpy as np
        image=np.full((140,220,3),255,np.uint8);image[10:130,10:210]=180
        cv2.putText(image,'B',(20,55),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2,cv2.LINE_AA)
        cv2.putText(image,'A',(110,55),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2,cv2.LINE_AA)
        fragment={'id':'f','reading_order':1,'source_lines':[{'text':'B','box':[18,30,45,59]},{'text':'A','box':[108,30,135,59]}]}
        reference=1-image.min(axis=2).astype(np.float32)/255
        return image,fragment,reference

    def observe(self,image,fragment,actual,reference):
        from scripts.measure_shared_passage_quality import observe_required_lines,content_joining_metrics
        lines=observe_required_lines(fragment,{'metadata':{'dpi':72}},image,actual>16/255,0,0,reference>16/255,actual,reference)
        metrics,order=content_joining_metrics({'groups':[{'id':'g','fragments':[fragment]}]},[{'id':'f','line_units':lines}])
        return lines,metrics,order

    def test_gray_B_identity_has_real_glyph_pattern_not_constant_binary(self):
        image,fragment,reference=self.fixture()
        lines,metrics,_=self.observe(image,fragment,reference,reference)
        self.assertTrue(all(l['matched'] for l in lines))
        self.assertEqual(metrics['unit_uniqueness']['numerator'],2)
        self.assertEqual(lines[0]['final_box'],[18,30,45,59])

    def test_erased_gray_B_fails_despite_retained_gray_background(self):
        image,fragment,reference=self.fixture();actual=reference.copy();actual[30:59,18:45]=75/255
        lines,metrics,_=self.observe(image,fragment,actual,reference)
        self.assertFalse(lines[0]['matched'])
        self.assertLess(metrics['unit_uniqueness']['numerator'],2)

    def test_filled_B_holes_fail_real_glyph_shape(self):
        import cv2,numpy as np
        image,fragment,reference=self.fixture();actual=reference.copy();roi=reference[30:59,18:45];glyph=roi>.6
        n,labels,stats,_=cv2.connectedComponentsWithStats((~glyph).astype(np.uint8),8)
        filled=0
        for cc in range(1,n):
            x,y,w,h,_=map(int,stats[cc])
            if x>0 and y>0 and x+w<glyph.shape[1] and y+h<glyph.shape[0]:actual[30:59,18:45][labels==cc]=1;filled+=1
        self.assertEqual(filled,2)
        lines,metrics,_=self.observe(image,fragment,actual,reference)
        self.assertTrue(not lines[0]['matched'] or lines[0]['final_box']!=[18,30,45,59])
        self.assertLess(metrics['unit_uniqueness']['numerator'],2)

    def test_swapped_gray_A_B_fails_order(self):
        image,fragment,reference=self.fixture();actual=reference.copy();actual[30:59,18:45]=reference[30:59,108:135];actual[30:59,108:135]=reference[30:59,18:45]
        _,_,order=self.observe(image,fragment,actual,reference)
        self.assertFalse(order['orders'][0]['passed'])

    def test_added_B_copy_over_original_gray_gradient_is_still_new(self):
        import numpy as np
        image,fragment,reference=self.fixture()
        ramp=np.linspace(180,240,29,dtype=np.uint8)[:,None]
        image[85:114,18:45,:]=ramp[:,:,None]
        reference=1-image.min(axis=2).astype(np.float32)/255;actual=reference.copy()
        glyph=reference[30:59,18:45]>.6
        actual[85:114,18:45][glyph]=1
        lines,metrics,_=self.observe(image,fragment,actual,reference)
        self.assertTrue(any(o['added_ink_occurrence'] for o in lines[0]['occurrences']))
        self.assertLess(metrics['unit_uniqueness']['numerator'],2)

    def test_added_gray_B_copy_fails_source_identity_uniqueness(self):
        image,fragment,reference=self.fixture();actual=reference.copy();actual[85:114,18:45]=reference[30:59,18:45]
        lines,metrics,_=self.observe(image,fragment,actual,reference)
        self.assertLess(metrics['unit_uniqueness']['numerator'],2)
        self.assertTrue(any(o['added_ink_occurrence'] for o in lines[0]['occurrences']))

class PeriodicGraySourceIdentityTests(unittest.TestCase):
    def fixture(self):
        import cv2,numpy as np
        image=np.full((140,220,3),255,np.uint8);image[10:130,10:210]=220
        for x in range(20,140,15):cv2.line(image,(x,40),(x+9,40),(0,0,0),2)
        reference=1-image.min(axis=2).astype(np.float32)/255
        actual=reference.copy();actual[(reference>0)&(reference<.5)]=60/255
        fragment={'id':'f','reading_order':1,'source_lines':[{'text':'--------','box':[15,25,145,50]}]}
        return image,fragment,reference,actual

    def observe(self,image,fragment,reference,actual):
        from scripts.measure_shared_passage_quality import observe_required_lines,content_joining_metrics
        lines=observe_required_lines(fragment,{'metadata':{'dpi':72}},image,actual>16/255,0,0,reference>200/255,actual,reference)
        return lines,content_joining_metrics({'groups':[{'id':'g','fragments':[fragment]}]},[{'id':'f','line_units':lines}])[0]

    def test_periodic_aliases_over_original_gray_dashes_are_not_added_copies(self):
        image,fragment,reference,actual=self.fixture();lines,metrics=self.observe(image,fragment,reference,actual)
        self.assertTrue(lines[0]['matched'])
        self.assertGreater(len(lines[0]['occurrences']),1)
        self.assertFalse(any(o['added_ink_occurrence'] for o in lines[0]['occurrences']))
        self.assertEqual(metrics['unit_uniqueness']['numerator'],1)

    def test_new_periodic_copy_inside_original_gray_material_is_added(self):
        image,fragment,reference,actual=self.fixture();actual[85:110,15:145]=actual[25:50,15:145]
        lines,metrics=self.observe(image,fragment,reference,actual)
        self.assertTrue(any(o['added_ink_occurrence'] for o in lines[0]['occurrences']))
        self.assertEqual(metrics['unit_uniqueness']['numerator'],0)

class ActualRecordSourceIdentityTests(unittest.TestCase):
    def run_case(self,swapped=False):
        import tempfile,json,hashlib,fitz,numpy as np
        from pathlib import Path
        from io import BytesIO
        from PIL import Image
        from edb_builder import ImageRecordSpec,build_image_record,build_edb
        from scripts.measure_shared_passage_quality import measure_shared
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);doc=fitz.open();groups=[];pages=[];problems=[];assets=[]
            for n,words in enumerate([['AAAAA BBBBB CCCCC','DDDDD EEEEE FFFFF','GGGGG HHHHH IIIII'],['WWWWW XXXXX YYYYY','ZZZZZ MMMMM NNNNN','OOOOO PPPPP QQQQQ']],1):
                page=doc.new_page(width=200,height=240)
                for i,text in enumerate(words):page.insert_text((15,40+i*40),text,fontsize=10)
                lines=[{'text':''.join(sp['text'] for sp in line['spans']),'box':list(line['bbox'])} for block in page.get_text('dict')['blocks'] for line in block.get('lines',[])]
                pix=page.get_pixmap(alpha=False);raw=Image.frombytes('RGB',(pix.width,pix.height),pix.samples);raw.save(root/f'raw{n}.png')
                arr=np.asarray(raw);rgba=np.full((240,200,4),248,np.uint8);rgba[...,3]=255-arr.min(axis=2);asset=Image.fromarray(rgba);assets.append(asset)
                Image.new('RGBA',(200,240),'black').save(root/f'preview{n}.png')
                pid=f'p{n}';pageid=f'source-page-{n:03d}';fragment={'id':f'f{n}','page':n,'reading_order':1,'box':[0,0,200,240],'source_lines':lines,'first_content_line_box':lines[0]['box']}
                groups.append({'id':f'g{n}','range':[n*2-1,n*2],'fragments':[fragment]});pages.append({'page_id':pageid,'metadata':{'dpi':72}})
                problems.append({'id':pid,'passageRange':{'start':n*2-1,'end':n*2},'sourcePageId':pageid,'sourceSegments':[{'sourcePageId':pageid,'bbox':[0,0,200,240]}],'imagePath':str(root/f'raw{n}.png'),'boardRenderPath':str(root/f'preview{n}.png')})
            source=root/'source.pdf';doc.save(source);doc.close();sha=hashlib.sha256(source.read_bytes()).hexdigest();records=[]
            for i,image in enumerate(reversed(assets) if swapped else assets):
                b=BytesIO();image.save(b,format='PNG');data=b.getvalue();records.append(build_image_record(ImageRecordSpec(i,data,data,.01,.01+i*.04,.4,.03)))
            edb=root/'output.edb';edb.write_bytes(build_edb(records,0));(root/'gt.json').write_text(json.dumps({'documents':[{'id':'source','source':{'path':str(source),'sha256':sha},'groups':groups}]}));(root/'pages.json').write_text(json.dumps(pages));(root/'session.json').write_text(json.dumps({'input_files':[str(source)],'problems':problems,'edb_parts':[{'edbPath':str(edb),'problemIds':['p1','p2']}]}))
            return measure_shared(root/'gt.json','source',root/'session.json',root/'pages.json',root/'out')

    def test_preview_tone_difference_is_diagnostic_actual_source_order_passes(self):
        report=self.run_case()
        self.assertTrue(all(x['max_channel_mean_abs_error']>.004 for x in report['details']['export_correspondence']))
        self.assertTrue(all(x['source_record_identity_passed'] for x in report['details']['export_correspondence']))
        self.assertNotIn('correct-order',report['hard_failures'])

    def test_actual_swapped_record_pixels_fail_source_identity_and_order(self):
        report=self.run_case(swapped=True)
        self.assertFalse(any(x['source_record_identity_passed'] for x in report['details']['export_correspondence']))
        self.assertIn('correct-order',report['hard_failures'])

class ChalkAndOpaqueMaterialTests(unittest.TestCase):
    def test_solid_chalk_button_with_alpha_letter_holes_retains_all_source_ink(self):
        import cv2,numpy as np
        from scripts.measure_shared_passage_quality import final_foreground
        from scripts.foreground_material_quality import has_rgb_paper_and_ink
        source=np.full((70,160),255,np.uint8);cv2.rectangle(source,(20,20),(130,48),0,-1);cv2.putText(source,'hi',(45,42),cv2.FONT_HERSHEY_SIMPLEX,.7,255,2,cv2.LINE_AA)
        actual=np.full((70,160,4),[248,249,246,0],np.uint8);actual[...,3]=255-source
        shared,surfaces=final_foreground(actual)
        self.assertEqual(surfaces,[]);self.assertFalse(has_rgb_paper_and_ink(actual,actual[...,3]>16))
        self.assertTrue(shared[source<200].all())
        self.assertFalse(shared[source==255].any())

    def test_real_opaque_rgb_paper_ink_erasure_and_intrusion_stay_visible(self):
        import numpy as np
        from scripts.measure_shared_passage_quality import final_foreground
        from scripts.foreground_material_quality import has_rgb_paper_and_ink
        actual=np.full((70,100,4),255,np.uint8);actual[20:40,20:30,:3]=0;actual[55:58,80:83,:3]=0
        shared,surfaces=final_foreground(actual)
        self.assertEqual(len(surfaces),1);self.assertTrue(has_rgb_paper_and_ink(actual,actual[...,3]>16));self.assertEqual(int(shared.sum()),209)
        actual[20:40,20:30,:3]=255
        erased,_=final_foreground(actual);self.assertFalse(erased[20:40,20:30].any());self.assertEqual(int(erased.sum()),9)
        actual[30:32,50:52,:3]=0
        extra,_=final_foreground(actual);self.assertTrue(extra[30:32,50:52].all());self.assertEqual(int(extra.sum()),13)

    def test_filling_chalk_button_letter_holes_is_still_glyph_damage(self):
        import cv2,numpy as np
        from scripts.glyph_shape_quality import glyph_shape_evidence
        source=np.zeros((30,80),np.uint8);cv2.putText(source,'B',(20,24),cv2.FONT_HERSHEY_SIMPLEX,.8,255,2,cv2.LINE_AA)
        expected=1-source.astype(np.float32)/255
        self.assertTrue(glyph_shape_evidence(expected,expected)['passed'])
        self.assertFalse(glyph_shape_evidence(expected,np.ones_like(expected))['passed'])

class OriginalContinuousIdentityRouteTests(unittest.TestCase):
    def test_unchanged_AA_pixels_keep_tone_identity_without_reverse_Otsu_veto(self):
        import numpy as np,hashlib
        from scripts.measure_shared_passage_quality import observe_required_lines
        source=np.zeros((24,220),np.float32)
        for x in range(10,190,20):source[5:15,x:x+8]=1
        source[5:15,192:202]=.20;source[8:12,195:199]=1
        actual=source.copy();actual[5:15,192:202]=1
        previous_actual=actual.copy()
        self.assertEqual(hashlib.sha256(actual.tobytes()).hexdigest(),hashlib.sha256(previous_actual.tobytes()).hexdigest())
        original=np.repeat((255-np.rint(source*255).astype(np.uint8))[:,:,None],3,axis=2)
        fragment={'id':'f','source_lines':[{'text':'Native antialiased printed source','box':[0,0,220,24]}]}
        units=observe_required_lines(fragment,{'metadata':{'dpi':72}},original,actual>16/255,0,0,source>55/255,actual,source)
        unit=units[0]
        self.assertFalse(unit['chosen_glyph_shape']['passed'])
        self.assertGreater(unit['chosen_continuous_identity']['ncc'],.95)
        self.assertTrue(all(c['ratio']==1 for c in unit['chosen_continuous_identity']['source_component_retention']))
        self.assertTrue(unit['matched'])

if __name__=='__main__': unittest.main()
