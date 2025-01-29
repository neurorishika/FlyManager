import os
from collections.abc import Iterator
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from flymanager.utils.utils import hex_to_rgb
from datetime import datetime
import segno

# Label Info:
# labels across
# labels down
# label size w/h
# label gutter across/down
# page margins left/top

labelInfo = {
    # 2.6 x 1 address labels
    5160: ( 3, 10, (187,  72), (11, 0), (14, 36)),
    5161: ( 2, 10, (288,  72), (0, 0), (18, 36)),
    # 4 x 2 address labels
    5163: ( 2,  5, (288, 144), (0, 0), (18, 36)),
    # 1.75 x 0.5 return address labels
    5167: ( 4, 20, (126,  36), (0, 0), (54, 36)),
    # 3.5 x 2 business cards
    5371: ( 2,  5, (252, 144), (0, 0), (54, 36)),
}

RETURN_ADDRESS = 5167
BUSINESS_CARDS = 5371

class AveryLabel:
    """
    Usage:
    
    label = AveryLabel(5160, row_first=True)
    label.open( "labels5160.pdf" )
    label.render( RenderAddress, 30 )
    label.close()
    
    'render' can either pass a callable, which receives the canvas object
    (with X,Y=0,0 at the lower right) or a string "form" name of a form
    previously created with canv.beginForm().

    To render, you can either create a template and tell me
    "go draw N of these templates" or provide a callback.
    Callback receives canvas, width, height.
    
    Or, pass a callable and an iterator.  We'll do one label
    per iteration of the iterator.
    """

    def __init__(self, label, row_first=False, **kwargs):
        data = labelInfo[label]
        self.across = data[0]
        self.down = data[1]
        self.size = data[2]
        self.labelsep = self.size[0] + data[3][0], self.size[1] + data[3][1]
        self.margins = data[4]
        self.topDown = True
        self.debug = False
        self.pagesize = LETTER
        self.position = 0
        self.row_first = row_first  # New attribute to determine row-first printing
        self.__dict__.update(kwargs)

    def open(self, filename):
        self.canvas = canvas.Canvas(filename, pagesize=self.pagesize)
        if self.debug:
            self.canvas.setPageCompression(0)
        self.canvas.setLineJoin(1)
        self.canvas.setLineCap(1)

    def topLeft(self, x=None, y=None):
        if x is None:
            x = self.position
        if y is None:
            if self.row_first:
                y, x = divmod(x, self.across)  # Move along rows first
            else:
                x, y = divmod(x, self.down)    # Default: Move along columns first

        return (
            self.margins[0] + x * self.labelsep[0],
            self.pagesize[1] - self.margins[1] - (y + 1) * self.labelsep[1]
        )

    def advance(self):
        self.position += 1
        if self.position == self.across * self.down:
            self.canvas.showPage()
            self.position = 0

    def close(self):
        if self.position:
            self.canvas.showPage()
        self.canvas.save()
        self.canvas = None

    def render(self, thing, count, *args):
        assert callable(thing) or isinstance(thing, str)
        canv = self.canvas
        for i in range(count):
            canv.saveState()
            canv.translate(*self.topLeft())
            if self.debug:
                canv.setLineWidth(0.25)
                canv.rect(0, 0, self.size[0], self.size[1])
            if callable(thing):
                thing(canv, self.size[0], self.size[1], *args)
            elif isinstance(thing, str):
                canv.doForm(thing)
            canv.restoreState()
            self.advance()

    def render_iterator(self, func, iterator):
        canv = self.canvas
        for chunk in iterator:
            canv.saveState()
            canv.translate(*self.topLeft())
            if self.debug:
                canv.setLineWidth(0.25)
                canv.rect(0, 0, self.size[0], self.size[1])
            func(canv, self.size[0], self.size[1], chunk)
            canv.restoreState()
            self.advance()




def render_stock_label(canvas, width, height, stock, uid, genotype, status, common_name, alt_name):
    '''
    LABEL TEMPLATE
    '''
    if stock != "":
        
        # write the common name as a watermark
        canvas.setFont("Courier-Bold", 7)
        canvas.setFillColorRGB(0.8, 0.8, 0.8)
        canvas.drawString(5, 5, common_name)

        # draw the stock
        canvas.setFont("Courier-Bold", 8)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(10, height - 15, f"{stock} | {datetime.now().strftime('%Y-%m-%d')}")
        
        # draw the genotype
        canvas.setFont("Courier", 6)
        if status == "Healthy":
            canvas.setFillColorRGB(*hex_to_rgb("#006400"))
        elif status == "Showing Issues":
            canvas.setFillColorRGB(*hex_to_rgb("#964B00"))
        elif status == "Needs refresh":
            canvas.setFillColorRGB(*hex_to_rgb("#8B0000"))
        # if the genotype is too long, split it into multiple lines
        genotype = genotype + "(" + alt_name + ")" if alt_name != "" else genotype
        split_genotype = [genotype[i:i+32] for i in range(0, len(genotype), 32)]
        for i, genotype in enumerate(split_genotype):
            canvas.drawString(10, height - 25 - i*7, f"{genotype}")
        
        # Add a small text with the UID above the QR code
        canvas.setFont("Courier-Bold", 6)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(width - 50, 60, f"{uid}")

        # add a qr code on the bottom right corner
        text = str(uid)
        
        # generate the qr code
        qr = segno.make(f"{text}")
        qr.save("temp/{0}.png".format(uid), scale=5)
        canvas.drawImage("temp/{}.png".format(uid),
                        width - 55, 12, width=45, height=45)

def render_cross_label(canvas, width, height, cross, uid, male_genotype, female_genotype, name, tray_info):
    '''
    CROSS LABEL TEMPLATE
    '''
    if cross != "":
        # Watermark the name
        canvas.setFont("Courier-Bold", 7)
        canvas.setFillColorRGB(0.8, 0.8, 0.8)
        canvas.drawString(5, 5, name)

        # Draw cross details (Tray info and creation date)
        canvas.setFont("Courier-Bold", 8)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(10, height - 15, f"{tray_info} | {datetime.now().strftime('%Y-%m-%d')}")

        # Set color based on status and display genotypes
        canvas.setFont("Courier", 6)

        # Display the male and female genotypes (split across multiple lines if necessary)
        male_genotype = f"M: {male_genotype}"
        female_genotype = f"F: {female_genotype}"

        split_male_genotype = [male_genotype[i:i+32] for i in range(0, len(male_genotype), 32)]
        split_female_genotype = [female_genotype[i:i+32] for i in range(0, len(female_genotype), 32)]

        for i, genotype in enumerate(split_male_genotype):
            canvas.drawString(10, height - 25 - i*7, f"{genotype}")
        
        for i, genotype in enumerate(split_female_genotype):
            canvas.drawString(10, height - 25 - (len(split_male_genotype) * 7) - i*7, f"{genotype}")

        # Add a small text with the UID above the QR code
        canvas.setFont("Courier-Bold", 6)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(width - 50, 60, f"{uid}")

        # Generate and add QR code at the bottom right corner
        qr = segno.make(f"{uid}")
        qr.save(f"temp/{uid}.png", scale=5)
        canvas.drawImage(f"temp/{uid}.png", width - 55, 12, width=45, height=45)

# create a general label pdf generator that accepts both stock and cross labels
def generate_label_pdf(filename, user_initial, selected_items, item_type, num_blank, num_labels, path="flymanager/static/generated_labels/", row_first=True, debug=False):
    """
    Generate labels for stocks and crosses in PDF format.
    
    Parameters:
    filename: str
        The name of the PDF file.
    user_initial: str
        The initials of the user.
    selected_items: list
        A list of dictionaries containing item details.
    item_type: list
        The type of item to generate labels for (stock or cross).
    num_blank: int
        The number of blank labels.
    num_labels: int
        The number of labels to generate.
    path: str
        The path to save the PDF.
    row_first: bool
        If True, prints the labels row-first.
    debug: bool
        If True, renders the label in debug mode.
    """
    if os.path.exists("temp/"):
        os.system("rm -r temp/")
    os.mkdir("temp/")

    # Initialize Avery label format
    label = AveryLabel(5160, row_first=row_first)

    if not os.path.exists(path):
        os.mkdir(path)

    if path[-1] != "/":
        path += "/"

    label.open(f"{path}{filename}.pdf")
    label.debug = debug

    # Render blank labels
    for i in range(num_blank):
        label.render(render_cross_label, 1, "", "", "", "", "", "")

    # Render item labels
    for i in range(num_labels):
        selected_item = selected_items[i]
        selected_item_type = item_type[i]
        if selected_item_type == "stock":
            genotype = selected_item["Genotype"]
            status = selected_item["Status"]
            name = selected_item["Name"]
            alt_name = selected_item["AltReference"]
            uid = selected_item["UniqueID"]
            tray_info = f"{selected_item['TrayID']}-{selected_item['TrayPosition']} ({user_initial})"
            label.render(
                render_stock_label, 1, 
                tray_info, 
                uid, 
                genotype, 
                status, 
                name, 
                alt_name
            )
        elif selected_item_type == "cross":
            male_genotype = selected_item["MaleGenotype"]
            female_genotype = selected_item["FemaleGenotype"]
            tray_info = f"{selected_item['TrayID']}-{selected_item['TrayPosition']} ({user_initial})"
            name = selected_item["Name"]
            uid = selected_item["UniqueID"]

            label.render(
                render_cross_label, 1, 
                tray_info, 
                uid, 
                male_genotype, 
                female_genotype, 
                name, 
                tray_info
            )

    label.close()