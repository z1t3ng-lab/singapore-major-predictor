#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>

@interface MajorForecastApp : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate>
@property NSWindow *window;
@property WKWebView *webView;
@property NSStackView *loading;
@property NSTextField *status;
@property NSProgressIndicator *spinner;
@property NSButton *retryButton;
@property NSDictionary *configuration;
@property NSTask *server;
@property NSPipe *input;
@property NSPipe *output;
@property NSMutableString *buffer;
@property NSURL *baseURL;
@property NSString *startupError;
@property NSFileHandle *log;
@property NSURL *logURL;
@property BOOL quitting;
@property BOOL terminationReplied;
@property NSMutableSet<WKDownload *> *downloads;
@property NSMapTable<WKDownload *, NSURL *> *destinations;
@property NSMapTable<WKDownload *, NSURL *> *temporaryFiles;
@end

@implementation MajorForecastApp

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    for (NSRunningApplication *other in [NSRunningApplication runningApplicationsWithBundleIdentifier:NSBundle.mainBundle.bundleIdentifier]) {
        if (other.processIdentifier != NSProcessInfo.processInfo.processIdentifier) {
            [other activateWithOptions:NSApplicationActivateAllWindows];
            [NSApp terminate:nil]; return;
        }
    }
    self.downloads = [NSMutableSet set];
    self.destinations = [NSMapTable strongToStrongObjectsMapTable];
    self.temporaryFiles = [NSMapTable strongToStrongObjectsMapTable];
    [self makeMenu]; [self makeWindow];
    NSURL *configURL = [NSBundle.mainBundle URLForResource:@"launcher" withExtension:@"json"];
    NSData *data = configURL ? [NSData dataWithContentsOfURL:configURL] : nil;
    self.configuration = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if ([self.configuration[@"bundled"] boolValue]) {
        NSMutableDictionary *config = self.configuration.mutableCopy;
        NSString *workspace = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"workspace"];
        config[@"projectPath"] = workspace;
        config[@"nodePath"] = [workspace stringByAppendingPathComponent:@"runtime/node"];
        config[@"dataPath"] = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Major Forecast/major.sqlite3"];
        self.configuration = config;
    }
    NSString *root = self.configuration[@"projectPath"];
    if (root.length) {
        NSString *folder = self.configuration[@"dataPath"] ? [self.configuration[@"dataPath"] stringByDeletingLastPathComponent] : [root stringByAppendingPathComponent:@".local-run"];
        [NSFileManager.defaultManager createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:nil];
        self.logURL = [NSURL fileURLWithPath:[folder stringByAppendingPathComponent:@"desktop.log"]];
        // Retain one previous launch without letting logs grow indefinitely.
        NSURL *previous = [NSURL fileURLWithPath:[folder stringByAppendingPathComponent:@"desktop.previous.log"]];
        [NSFileManager.defaultManager removeItemAtURL:previous error:nil];
        [NSFileManager.defaultManager moveItemAtURL:self.logURL toURL:previous error:nil];
        [NSFileManager.defaultManager createFileAtPath:self.logURL.path contents:nil attributes:nil];
        self.log = [NSFileHandle fileHandleForWritingAtPath:self.logURL.path];
    }
    [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];
    [self startServer];
}

- (NSMenu *)submenu:(NSString *)title parent:(NSMenu *)parent {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:nil keyEquivalent:@""];
    item.submenu = [[NSMenu alloc] initWithTitle:title]; [parent addItem:item]; return item.submenu;
}
- (void)add:(NSString *)title action:(SEL)action key:(NSString *)key menu:(NSMenu *)menu {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key];
    item.target = self; [menu addItem:item];
}
- (void)makeMenu {
    NSMenu *main = [NSMenu new];
    NSMenu *app = [self submenu:@"Major Forecast" parent:main];
    [self add:@"About Major Forecast" action:@selector(about:) key:@"" menu:app];
    [app addItem:NSMenuItem.separatorItem];
    [app addItemWithTitle:@"Hide Major Forecast" action:@selector(hide:) keyEquivalent:@"h"];
    [app addItem:NSMenuItem.separatorItem];
    [self add:@"Quit Major Forecast" action:@selector(quit:) key:@"q" menu:app];
    NSMenu *file = [self submenu:@"File" parent:main];
    [self add:@"Show Saved Data" action:@selector(showData:) key:@"" menu:file];
    [self add:@"Close Window" action:@selector(close:) key:@"w" menu:file];
    NSMenu *edit = [self submenu:@"Edit" parent:main];
    for (NSArray *entry in @[@[@"Undo", @"undo:", @"z"], @[@"Redo", @"redo:", @"Z"], @[@"Cut", @"cut:", @"x"], @[@"Copy", @"copy:", @"c"], @[@"Paste", @"paste:", @"v"], @[@"Select All", @"selectAll:", @"a"]])
        [edit addItemWithTitle:entry[0] action:NSSelectorFromString(entry[1]) keyEquivalent:entry[2]];
    NSMenu *view = [self submenu:@"View" parent:main];
    [self add:@"Reload" action:@selector(reload:) key:@"r" menu:view];
    [self add:@"Zoom In" action:@selector(zoomIn:) key:@"+" menu:view];
    [self add:@"Zoom Out" action:@selector(zoomOut:) key:@"-" menu:view];
    [self add:@"Actual Size" action:@selector(actualSize:) key:@"0" menu:view];
    NSMenu *help = [self submenu:@"Help" parent:main];
    [self add:@"Using Major Forecast" action:@selector(guide:) key:@"" menu:help];
    [self add:@"Show Startup Log" action:@selector(showLog:) key:@"" menu:help];
    NSApp.mainMenu = main;
}

- (void)makeWindow {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1360, 880)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Major Forecast";
    self.window.minSize = NSMakeSize(760, 560);
    self.window.releasedWhenClosed = NO;
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    self.window.backgroundColor = [NSColor colorWithRed:0.035 green:0.043 blue:0.059 alpha:1];
    [self.window center]; [self.window setFrameAutosaveName:@"MajorForecastDesktopWindow"];
    NSView *container = [NSView new]; self.window.contentView = container;
    WKWebViewConfiguration *settings = [WKWebViewConfiguration new];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:settings];
    self.webView.navigationDelegate = self; self.webView.UIDelegate = self;
    self.webView.translatesAutoresizingMaskIntoConstraints = NO;
    self.webView.hidden = YES;
    [container addSubview:self.webView];
    [NSLayoutConstraint activateConstraints:@[
        [self.webView.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
        [self.webView.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
        [self.webView.topAnchor constraintEqualToAnchor:container.topAnchor],
        [self.webView.bottomAnchor constraintEqualToAnchor:container.bottomAnchor]]];
    NSImageView *icon = [NSImageView imageViewWithImage:NSApp.applicationIconImage];
    [icon.widthAnchor constraintEqualToConstant:86].active = YES;
    [icon.heightAnchor constraintEqualToConstant:86].active = YES;
    NSTextField *heading = [NSTextField labelWithString:@"Your tournament. One place."];
    heading.font = [NSFont systemFontOfSize:27 weight:NSFontWeightSemibold];
    self.status = [NSTextField wrappingLabelWithString:@"Starting Major Forecast…"];
    self.status.alignment = NSTextAlignmentCenter;
    self.status.textColor = NSColor.secondaryLabelColor;
    self.status.font = [NSFont systemFontOfSize:14];
    self.status.preferredMaxLayoutWidth = 460;
    self.spinner = [NSProgressIndicator new]; self.spinner.style = NSProgressIndicatorStyleSpinning;
    [self.spinner startAnimation:nil];
    self.retryButton = [NSButton buttonWithTitle:@"Try Again" target:self action:@selector(retry:)];
    self.retryButton.bezelStyle = NSBezelStyleRounded; self.retryButton.hidden = YES;
    self.loading = [NSStackView stackViewWithViews:@[icon, heading, self.status, self.spinner, self.retryButton]];
    self.loading.orientation = NSUserInterfaceLayoutOrientationVertical;
    self.loading.alignment = NSLayoutAttributeCenterX; self.loading.spacing = 20;
    self.loading.translatesAutoresizingMaskIntoConstraints = NO;
    [container addSubview:self.loading];
    [NSLayoutConstraint activateConstraints:@[
        [self.loading.centerXAnchor constraintEqualToAnchor:container.centerXAnchor],
        [self.loading.centerYAnchor constraintEqualToAnchor:container.centerYAnchor],
        [self.loading.widthAnchor constraintLessThanOrEqualToConstant:500]]];
}

- (void)writeLog:(NSString *)text {
    @try { [self.log writeData:[text dataUsingEncoding:NSUTF8StringEncoding]]; } @catch (NSException *ignored) {}
}
- (void)startServer {
    if (self.server.running) return;
    NSString *root = self.configuration[@"projectPath"], *node = self.configuration[@"nodePath"];
    if (!root || !node) { [self showError:@"The desktop configuration is missing. Rebuild and reinstall the desktop app from its project."]; return; }
    if (![NSFileManager.defaultManager fileExistsAtPath:[root stringByAppendingPathComponent:@"desktop/server.mjs"]]) {
        [self showError:[NSString stringWithFormat:@"The project folder has moved or is unavailable. Restore it at:\n%@\n\nOr rebuild and reinstall the desktop app from the new location.", root]]; return;
    }
    if (![NSFileManager.defaultManager isExecutableFileAtPath:node]) { [self showError:@"Node.js is unavailable. Restore its installation or rebuild this desktop app after moving Node.js."]; return; }
    self.output.fileHandleForReading.readabilityHandler = nil;
    self.buffer = [NSMutableString string]; self.baseURL = nil; self.startupError = nil;
    self.loading.hidden = NO; self.webView.hidden = YES; self.retryButton.hidden = YES;
    self.status.stringValue = @"Opening your tournament workspace…";
    self.spinner.hidden = NO; [self.spinner startAnimation:nil];
    NSTask *task = [NSTask new]; self.server = task;
    task.executableURL = [NSURL fileURLWithPath:node];
    task.arguments = @[[root stringByAppendingPathComponent:@"desktop/server.mjs"]];
    task.currentDirectoryURL = [NSURL fileURLWithPath:root];
    NSMutableDictionary *environment = NSProcessInfo.processInfo.environment.mutableCopy;
    environment[@"PATH"] = [NSString stringWithFormat:@"%@:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", node.stringByDeletingLastPathComponent];
    [environment removeObjectForKey:@"NODE_OPTIONS"];
    if (self.configuration[@"dataPath"]) environment[@"MAJOR_DB_PATH"] = self.configuration[@"dataPath"];
    task.environment = environment;
    self.input = [NSPipe pipe]; self.output = [NSPipe pipe];
    task.standardInput = self.input; task.standardOutput = self.output; task.standardError = self.output;
    __weak MajorForecastApp *weakSelf = self;
    __weak NSTask *weakTask = task;
    self.output.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = handle.availableData;
        if (!data.length) { handle.readabilityHandler = nil; return; }
        NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        if (text) dispatch_async(dispatch_get_main_queue(), ^{ if (weakSelf.server == weakTask) [weakSelf receive:text]; });
    };
    task.terminationHandler = ^(NSTask *ended) {
        dispatch_async(dispatch_get_main_queue(), ^{
            MajorForecastApp *app = weakSelf;
            if (app.server != ended) return;
            if (app.quitting) { [app finishQuitting]; return; }
            [app showError:app.startupError ?: @"The local service stopped. Your saved tournament is still in the database. Choose Try Again, or open Help → Show Startup Log for details."];
        });
    };
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) [self showError:[@"Couldn’t start Major Forecast: " stringByAppendingString:error.localizedDescription]];
}
- (void)receive:(NSString *)text {
    [self writeLog:text]; [self.buffer appendString:text];
    NSRange newline;
    while ((newline = [self.buffer rangeOfString:@"\n"]).location != NSNotFound) {
        NSString *line = [self.buffer substringToIndex:newline.location];
        [self.buffer deleteCharactersInRange:NSMakeRange(0, newline.location + 1)];
        NSRange space = [line rangeOfString:@" "];
        if (space.location == NSNotFound) continue;
        NSString *prefix = [line substringToIndex:space.location];
        NSData *json = [[line substringFromIndex:space.location + 1] dataUsingEncoding:NSUTF8StringEncoding];
        id value = [NSJSONSerialization JSONObjectWithData:json options:NSJSONReadingFragmentsAllowed error:nil];
        if ([prefix isEqualToString:@"MAJOR_STATUS"] && [value isKindOfClass:NSString.class]) self.status.stringValue = value;
        if ([prefix isEqualToString:@"MAJOR_ERROR"] && [value isKindOfClass:NSString.class]) { self.startupError = value; [self showError:value]; }
        if ([prefix isEqualToString:@"MAJOR_READY"] && [value isKindOfClass:NSDictionary.class]) {
            NSURL *url = [NSURL URLWithString:value[@"url"]];
            if ([url.host isEqualToString:@"127.0.0.1"] && [url.scheme isEqualToString:@"http"] && url.port) {
                self.baseURL = url; [self.webView loadRequest:[NSURLRequest requestWithURL:url]];
            }
        }
    }
}
- (void)showError:(NSString *)message {
    [self writeLog:[NSString stringWithFormat:@"ERROR: %@\n", message]];
    self.status.stringValue = message;
    self.loading.hidden = NO; self.webView.hidden = YES;
    [self.spinner stopAnimation:nil]; self.spinner.hidden = YES;
    self.retryButton.hidden = NO;
}
- (BOOL)localURL:(NSURL *)url {
    return self.baseURL && [url.scheme isEqualToString:self.baseURL.scheme] && [url.host isEqualToString:self.baseURL.host] && [url.port isEqualToNumber:self.baseURL.port];
}
- (BOOL)localBlob:(NSURL *)url {
    return self.baseURL && [url.absoluteString hasPrefix:[NSString stringWithFormat:@"blob:%@/", self.baseURL.absoluteString]];
}
- (void)openExternal:(NSURL *)url {
    if ([@[@"https", @"http", @"mailto"] containsObject:url.scheme]) [NSWorkspace.sharedWorkspace openURL:url];
}
- (void)webView:(WKWebView *)view didFinishNavigation:(WKNavigation *)navigation {
    if (![self localURL:view.URL]) return;
    self.loading.hidden = YES; self.webView.hidden = NO; [self.spinner stopAnimation:nil];
    [self.window makeFirstResponder:self.webView];
}
- (void)webView:(WKWebView *)view didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code != NSURLErrorCancelled) [self showError:[@"Couldn’t open the tournament: " stringByAppendingString:error.localizedDescription]];
}
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView { [self showError:@"The app window needs to reload. Choose Try Again to reopen your saved tournament."]; }
- (void)webView:(WKWebView *)view decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = action.request.URL;
    if ([self localURL:url] || [self localBlob:url]) {
        decisionHandler(action.shouldPerformDownload || [self localBlob:url] ? WKNavigationActionPolicyDownload : WKNavigationActionPolicyAllow);
    } else {
        if (action.navigationType == WKNavigationTypeLinkActivated) [self openExternal:url];
        decisionHandler(WKNavigationActionPolicyCancel);
    }
}
- (void)webView:(WKWebView *)view decidePolicyForNavigationResponse:(WKNavigationResponse *)response decisionHandler:(void (^)(WKNavigationResponsePolicy))decisionHandler {
    NSString *disposition = [response.response isKindOfClass:NSHTTPURLResponse.class] ? [(NSHTTPURLResponse *)response.response valueForHTTPHeaderField:@"Content-Disposition"] : @"";
    decisionHandler(!response.canShowMIMEType || [disposition.lowercaseString containsString:@"attachment"] ? WKNavigationResponsePolicyDownload : WKNavigationResponsePolicyAllow);
}
- (WKWebView *)webView:(WKWebView *)view createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    if ([self localURL:action.request.URL]) [view loadRequest:action.request]; else [self openExternal:action.request.URL];
    return nil;
}
- (void)webView:(WKWebView *)view runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))completion {
    NSOpenPanel *panel = [NSOpenPanel openPanel]; panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.allowedContentTypes = @[UTTypeJSON, UTTypeCommaSeparatedText, UTTypePlainText];
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completion(result == NSModalResponseOK ? panel.URLs : nil); }];
}
- (void)webView:(WKWebView *)view runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))completion {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"Major Forecast"; alert.informativeText = message;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) { completion(); }];
}
- (void)webView:(WKWebView *)view runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))completion {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"Confirm change"; alert.informativeText = message;
    [alert addButtonWithTitle:@"Confirm"]; [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) { completion(response == NSAlertFirstButtonReturn); }];
}
- (void)trackDownload:(WKDownload *)download { [self.downloads addObject:download]; download.delegate = self; }
- (void)webView:(WKWebView *)view navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download { [self trackDownload:download]; }
- (void)webView:(WKWebView *)view navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download { [self trackDownload:download]; }
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)name completionHandler:(void (^)(NSURL *))completion {
    NSSavePanel *panel = [NSSavePanel savePanel]; panel.nameFieldStringValue = name.lastPathComponent;
    panel.canCreateDirectories = YES;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK) { completion(nil); return; }
        NSURL *temp = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:[@"major-export-" stringByAppendingString:NSUUID.UUID.UUIDString]]];
        [self.destinations setObject:panel.URL forKey:download];
        [self.temporaryFiles setObject:temp forKey:download]; completion(temp);
    }];
}
- (void)downloadDidFinish:(WKDownload *)download {
    NSURL *temp = [self.temporaryFiles objectForKey:download], *destination = [self.destinations objectForKey:download];
    NSError *error = nil;
    if (temp && destination) {
        NSData *data = [NSData dataWithContentsOfURL:temp options:0 error:&error];
        if (data) [data writeToURL:destination options:NSDataWritingAtomic error:&error];
        if (error) [self downloadError:error]; else [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[destination]];
    }
    [self clearDownload:download];
}
- (void)clearDownload:(WKDownload *)download {
    NSURL *temp = [self.temporaryFiles objectForKey:download];
    if (temp) [NSFileManager.defaultManager removeItemAtURL:temp error:nil];
    [self.temporaryFiles removeObjectForKey:download]; [self.destinations removeObjectForKey:download]; [self.downloads removeObject:download];
}
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)data {
    [self clearDownload:download]; if (error.code != NSURLErrorCancelled) [self downloadError:error];
}
- (void)downloadError:(NSError *)error {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"The file could not be saved"; alert.informativeText = error.localizedDescription;
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}

- (void)retry:(id)sender {
    if (!self.server.running) [self startServer];
    else if (self.baseURL) { self.retryButton.hidden = YES; self.spinner.hidden = NO; [self.spinner startAnimation:nil]; [self.webView loadRequest:[NSURLRequest requestWithURL:self.baseURL]]; }
}
- (void)reload:(id)sender { if (self.baseURL) [self.webView reload]; else [self retry:nil]; }
- (void)zoomIn:(id)sender { self.webView.pageZoom = MIN(2, self.webView.pageZoom + 0.1); }
- (void)zoomOut:(id)sender { self.webView.pageZoom = MAX(0.7, self.webView.pageZoom - 0.1); }
- (void)actualSize:(id)sender { self.webView.pageZoom = 1; }
- (void)close:(id)sender { [self.window performClose:nil]; }
- (void)quit:(id)sender { [NSApp terminate:nil]; }
- (void)showLog:(id)sender { if (self.logURL) [NSWorkspace.sharedWorkspace openURL:self.logURL]; }
- (void)showData:(id)sender {
    NSString *path = self.configuration[@"dataPath"] ?: [self.configuration[@"projectPath"] stringByAppendingPathComponent:@"backend/data/major.sqlite3"];
    if (path) [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[[NSURL fileURLWithPath:path]]];
}
- (void)about:(id)sender {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"Major Forecast";
    alert.informativeText = @"Version 1.2 · Local macOS app\n\nYour Singapore Major tournament, simulations, and confirmed results in one desktop workspace. Includes saved scenarios, profiles, calibration, and recovery backups.";
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}
- (void)guide:(id)sender {
    NSAlert *alert = [NSAlert new]; alert.messageText = @"Your tournament workspace";
    alert.informativeText = @"Use the sidebar to explore each stage and the playoffs. Import participants in Method & data, then choose Simulate Tournament. Set Result records a finished match and updates the remaining predictions.\n\nCSV and JSON exports open a Save dialog. All confirmed results stay in your local database when you quit.\n\nDouble-click Major Forecast to reopen anytime. The app starts and stops its own services. The bundled edition includes its own runtimes. File → Show Saved Data reveals your local database.";
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag { [self.window makeKeyAndOrderFront:nil]; return YES; }
- (void)finishQuitting { if (!self.terminationReplied) { self.terminationReplied = YES; [NSApp replyToApplicationShouldTerminate:YES]; } }
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (!self.server.running) return NSTerminateNow;
    self.quitting = YES;
    [self.input.fileHandleForWriting closeFile]; [self.server terminate];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [self finishQuitting]; });
    return NSTerminateLater;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    self.quitting = YES;
    self.output.fileHandleForReading.readabilityHandler = nil;
    [self.input.fileHandleForWriting closeFile];
    if (self.server.running) [self.server terminate];
    [self.log closeFile];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        MajorForecastApp *delegate = [MajorForecastApp new]; app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular]; [app run];
    }
    return 0;
}
