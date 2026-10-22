#import <Cocoa/Cocoa.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *folder = @(argv[1]);
        [NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:nil];
        for (NSNumber *size in @[@16, @32, @128, @256, @512]) {
            for (int scale = 1; scale <= 2; scale++) {
                NSInteger pixels = size.integerValue * scale;
                NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:pixels pixelsHigh:pixels bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
                [NSGraphicsContext saveGraphicsState];
                [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:rep]];
                NSAffineTransform *transform = [NSAffineTransform transform]; [transform scaleBy:pixels / 1024.0]; [transform concat];
                NSBezierPath *base = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(56, 56, 912, 912) xRadius:205 yRadius:205];
                NSGradient *gradient = [[NSGradient alloc] initWithStartingColor:[NSColor colorWithRed:0.12 green:0.18 blue:0.23 alpha:1] endingColor:[NSColor colorWithRed:0.025 green:0.045 blue:0.065 alpha:1]];
                [gradient drawInBezierPath:base angle:105];
                [[NSColor colorWithWhite:1 alpha:0.12] setStroke]; base.lineWidth = 5; [base stroke];
                NSColor *accent = [NSColor colorWithRed:0.73 green:0.96 blue:0.34 alpha:1];
                [accent setStroke];
                NSBezierPath *bracket = [NSBezierPath bezierPath]; bracket.lineWidth = 36; bracket.lineCapStyle = NSLineCapStyleRound; bracket.lineJoinStyle = NSLineJoinStyleRound;
                [bracket moveToPoint:NSMakePoint(240, 700)]; [bracket lineToPoint:NSMakePoint(365, 700)]; [bracket lineToPoint:NSMakePoint(365, 330)]; [bracket lineToPoint:NSMakePoint(240, 330)];
                [bracket moveToPoint:NSMakePoint(365, 515)]; [bracket lineToPoint:NSMakePoint(490, 515)]; [bracket lineToPoint:NSMakePoint(610, 650)]; [bracket lineToPoint:NSMakePoint(780, 650)];
                [bracket moveToPoint:NSMakePoint(690, 740)]; [bracket lineToPoint:NSMakePoint(780, 650)]; [bracket lineToPoint:NSMakePoint(690, 560)]; [bracket stroke];
                [accent setFill]; [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(207, 667, 66, 66)] fill]; [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(207, 297, 66, 66)] fill];
                NSDictionary *attributes = @{NSFontAttributeName:[NSFont systemFontOfSize:76 weight:NSFontWeightBold], NSForegroundColorAttributeName:[NSColor colorWithWhite:0.84 alpha:1], NSKernAttributeName:@11};
                [@"MAJOR" drawAtPoint:NSMakePoint(493, 294) withAttributes:attributes];
                [NSGraphicsContext restoreGraphicsState];
                NSString *name = [NSString stringWithFormat:@"icon_%@x%@%@.png", size, size, scale == 2 ? @"@2x" : @""];
                [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:[folder stringByAppendingPathComponent:name] atomically:YES];
            }
        }
    }
    return 0;
}
